#!/usr/bin/env python3
"""
build_rivers_geom.py
====================
Genera backend/data/ar_rivers_geom.geojson combinando:
  A) HydroRIVERS v10 (Strahler ≥ 4, ~16k segmentos para Argentina)
  B) Cabeceras OSM (Strahler 1-3, ~17k segmentos de ríos con nombre)

Pipeline:
  1. Lee HydroRIVERS shapefile filtrado a Argentina
  2. Asigna nombres desde ar_rivers_names.json (cross-reference por ID)
  3. Descarga de Overpass: waterway=river|stream con nombre en Argentina
  4. Para cada segmento OSM:
     - Si está cubierto por HydroRIVERS (dentro de 300m), descarta
     - Si no está cubierto, agrega como segmento OSM-only (cabecera)
  5. Cierra gaps entre segmentos adyacentes con endpoint clustering (100m)
  6. Escribe ar_rivers_geom.geojson

Uso:
    cd backend/scripts
    python3 build_rivers_geom.py --hr /ruta/a/HydroRIVERS_v10_sa_shp/

Requisitos:
    pip install shapely pyshp
    curl (disponible en macOS por defecto)

    Los shapefiles de HydroRIVERS (América del Sur) se descargan gratis desde:
    https://www.hydrosheds.org/products/hydrorivers
    → HydroRIVERS_v10_sa.zip (South America, ~150 MB)

Tiempo estimado: ~30-60 minutos (descarga OSM + procesamiento shapefile)

Notas sobre el esquema de IDs:
  - HydroRIVERS: IDs originales del dataset (61xxxxxx para Argentina)
  - OSM-only:   IDs sintéticos desde 90_000_000 en adelante

Notas sobre propiedades:
  - s: Strahler order (1-9; OSM-only usan 2=stream o 4=river)
  - q: caudal medio estimado (m³/s, de HydroRIVERS DIS_AV_CMS)
  - name: nombre del río
  - osm_only: True si el segmento viene solo de OSM
"""

import argparse
import json
import math
import os
import subprocess
import time

try:
    import shapefile  # pyshp
    HAS_PYSHP = True
except ImportError:
    HAS_PYSHP = False
    print('AVISO: pyshp no instalado. Solo se procesarán segmentos OSM.')
    print('       Para usar HydroRIVERS: pip install pyshp')

from shapely.geometry import LineString, MultiLineString, Point, mapping, shape
from shapely.ops import linemerge, unary_union
from shapely.strtree import STRtree

# ── Rutas ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.path.join(SCRIPT_DIR, '..', 'data')
OUT_PATH   = os.path.join(DATA_DIR, 'ar_rivers_geom.geojson')
NAMES_PATH = os.path.join(DATA_DIR, 'ar_rivers_names.json')
AR_PATH    = os.path.join(DATA_DIR, 'argentina.geojson')

# ── Parámetros ─────────────────────────────────────────────────────────────
# Strahler mínimo de HydroRIVERS para incluir como segmento principal
HR_MIN_STRAHLER = 4

# Distancia máxima (grados) para considerar que OSM cubre un segmento HR
OSM_SNAP_BUFFER_DEG = 300 / 111_000  # 300 metros en grados aprox.

# Distancia máxima (grados) para unir endpoints de segmentos adyacentes
ENDPOINT_CLUSTER_DEG = 100 / 111_000  # 100 metros

# Bbox Argentina (S, W, N, E)
AR_BBOX = (-56, -74, -21, -53)

# ID base para segmentos OSM-only
OSM_ID_BASE = 90_000_000


# ── Overpass ───────────────────────────────────────────────────────────────

def overpass_query(query: str, retries: int = 2) -> list:
    """Ejecuta query Overpass. Retorna lista de elements."""
    with open('/tmp/_overpass_rivers.txt', 'w') as f:
        f.write(query)
    for attempt in range(retries + 1):
        r = subprocess.run(
            ['curl', '-s', '-H', 'Accept: application/json',
             '-A', 'AppAgua/1.0 build_rivers_geom.py',
             '-X', 'POST', '--data-urlencode', 'data@/tmp/_overpass_rivers.txt',
             'https://overpass-api.de/api/interpreter'],
            capture_output=True, timeout=300
        )
        try:
            d = json.loads(r.stdout)
            if 'remark' in d and 'timeout' in d['remark']:
                print(f'  Timeout (intento {attempt+1}/{retries+1}), reintentando...')
                time.sleep(10)
                continue
            return d.get('elements', [])
        except Exception as e:
            print(f'  Error: {e}')
            time.sleep(5)
    return []


def download_osm_rivers(bbox: tuple, chunk_size_deg: float = 5.0) -> list:
    """
    Descarga waterway=river|stream con nombre en chunks para evitar timeout.
    bbox = (S, W, N, E)
    """
    s, w, n, e = bbox
    all_els = []
    lat_chunks = range(int(s), int(n), int(chunk_size_deg))
    lng_chunks = range(int(w), int(e), int(chunk_size_deg))
    total = len(list(lat_chunks)) * len(list(lng_chunks))
    done = 0
    for lat in range(int(s), int(n), int(chunk_size_deg)):
        for lng in range(int(w), int(e), int(chunk_size_deg)):
            lat_max = min(lat + chunk_size_deg, n)
            lng_max = min(lng + chunk_size_deg, e)
            q = f"""[out:json][timeout:90];
(
  way["waterway"~"river|stream"]["name"]({lat},{lng},{lat_max},{lng_max});
);
out geom;
"""
            done += 1
            print(f'  Chunk {done}/{total}: lat({lat},{lat_max:.0f}) lng({lng},{lng_max:.0f})', end=' ')
            els = overpass_query(q, retries=1)
            print(f'→ {len(els)} ways')
            all_els.extend(els)
            time.sleep(1)  # respetar rate limit Overpass
    return all_els


# ── HydroRIVERS ───────────────────────────────────────────────────────────

def load_hydrorivers(shp_path: str, names_dict: dict) -> list[dict]:
    """
    Lee HydroRIVERS shapefile y retorna lista de features dict con:
      id, s (Strahler), q (caudal), name, geometry (shapely LineString)
    Solo incluye segmentos con Strahler >= HR_MIN_STRAHLER.
    """
    if not HAS_PYSHP:
        return []
    print(f'  Leyendo shapefile: {shp_path}')
    sf = shapefile.Reader(shp_path)
    fields = [f[0] for f in sf.fields[1:]]
    features = []
    for sr in sf.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        strahler = int(rec.get('ORD_STRA', 0))
        if strahler < HR_MIN_STRAHLER:
            continue
        hyriv_id = int(rec.get('HYRIV_ID', 0))
        dis_av = float(rec.get('DIS_AV_CMS', 0))
        q = max(1, min(9999, round(dis_av)))
        name = names_dict.get(str(hyriv_id), '')
        coords = sr.shape.points
        if len(coords) < 2:
            continue
        geom = LineString(coords)
        if not geom.is_valid:
            continue
        features.append({
            'id': hyriv_id,
            's': strahler,
            'q': q,
            'name': name,
            'geometry': geom,
        })
    print(f'  → {len(features)} segmentos HR Strahler ≥ {HR_MIN_STRAHLER}')
    return features


# ── OSM processing ────────────────────────────────────────────────────────

def osm_to_linestrings(elements: list) -> list[dict]:
    """Convierte elementos OSM a dicts con geometry (LineString) y name."""
    result = []
    for el in elements:
        name = el.get('tags', {}).get('name', '')
        wtype = el.get('tags', {}).get('waterway', 'stream')
        coords = [(p['lon'], p['lat']) for p in el.get('geometry', [])]
        if len(coords) < 2:
            continue
        try:
            geom = LineString(coords)
            if not geom.is_valid or geom.length < 1e-6:
                continue
            is_river = (wtype == 'river')
            result.append({
                'name': name,
                'is_river': is_river,
                'geometry': geom,
            })
        except Exception:
            pass
    return result


def extract_osm_headwaters(osm_lines: list, hr_geoms: list) -> list[dict]:
    """
    Retorna los segmentos OSM que NO están cubiertos por HydroRIVERS.
    "Cubierto" = dentro del buffer de OSM_SNAP_BUFFER_DEG de cualquier segmento HR.
    Usa STRtree para eficiencia.
    """
    if not hr_geoms:
        # Sin HR, todos los OSM son headwaters
        return osm_lines

    print('  Construyendo índice espacial HR...')
    hr_buffer_geoms = [g.buffer(OSM_SNAP_BUFFER_DEG) for g in hr_geoms]
    hr_union = unary_union(hr_buffer_geoms)

    headwaters = []
    print(f'  Procesando {len(osm_lines)} segmentos OSM...')
    for i, ol in enumerate(osm_lines):
        if i % 5000 == 0:
            print(f'  {i}/{len(osm_lines)}')
        try:
            diff = ol['geometry'].difference(hr_union)
            if diff.is_empty or diff.length < 1e-5:
                continue
            # Mantener solo la parte sin cobertura HR
            ol_copy = dict(ol)
            ol_copy['geometry'] = diff
            headwaters.append(ol_copy)
        except Exception:
            pass
    print(f'  → {len(headwaters)} segmentos OSM-only (cabeceras)')
    return headwaters


def close_endpoint_gaps(features: list) -> list:
    """
    Une vértices finales de segmentos adyacentes que estén a menos de
    ENDPOINT_CLUSTER_DEG entre sí.
    """
    # Recopilar todos los endpoints
    endpoints = []
    for f in features:
        geom = shape(f['geometry'])
        if geom.geom_type == 'LineString':
            coords = list(geom.coords)
            endpoints.append((f, 0, coords[0]))
            endpoints.append((f, -1, coords[-1]))

    print(f'  Cerrando gaps en {len(features)} segmentos...')
    gap_tree = STRtree([Point(e[2]) for e in endpoints])
    closed = 0
    for feat in features:
        geom = shape(feat['geometry'])
        if geom.geom_type != 'LineString':
            continue
        coords = list(geom.coords)
        for idx in [0, -1]:
            pt = Point(coords[idx])
            nearby = gap_tree.query(pt.buffer(ENDPOINT_CLUSTER_DEG))
            for j in nearby:
                ep_feat, ep_idx, ep_coords = endpoints[j]
                if ep_feat is feat:
                    continue
                ep_pt = Point(ep_coords)
                if pt.distance(ep_pt) < ENDPOINT_CLUSTER_DEG:
                    # Snap: mover el endpoint al promedio
                    mid = ((coords[idx][0] + ep_coords[0]) / 2,
                           (coords[idx][1] + ep_coords[1]) / 2)
                    coords[idx] = mid
                    closed += 1
                    break
        feat['geometry'] = mapping(LineString(coords))
    print(f'  → {closed} gaps cerrados')
    return features


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Build ar_rivers_geom.geojson')
    parser.add_argument('--hr', metavar='SHP_DIR',
                        help='Directorio del shapefile HydroRIVERS SA (.shp)')
    parser.add_argument('--skip-osm', action='store_true',
                        help='No descargar OSM (solo usar HR)')
    args = parser.parse_args()

    # ── Cargar nombres ─────────────────────────────────────────────────────
    print('── Cargando ar_rivers_names.json ──')
    with open(NAMES_PATH) as f:
        names_dict = json.load(f)
    print(f'  {len(names_dict)} nombres cargados')

    # ── HydroRIVERS ────────────────────────────────────────────────────────
    hr_features = []
    if args.hr:
        shp_files = [f for f in os.listdir(args.hr) if f.endswith('.shp')]
        if not shp_files:
            print(f'ERROR: No se encontró .shp en {args.hr}')
        else:
            shp_path = os.path.join(args.hr, shp_files[0])
            print(f'\n── Cargando HydroRIVERS: {shp_path} ──')
            hr_features = load_hydrorivers(shp_path, names_dict)
    else:
        print('\n── Sin HydroRIVERS (--hr no especificado) ──')
        print('   Solo se generarán segmentos OSM.')

    hr_geoms = [f['geometry'] for f in hr_features]

    # ── OSM headwaters ─────────────────────────────────────────────────────
    osm_features_raw = []
    if not args.skip_osm:
        print('\n── Descargando ríos de OSM (Overpass) ──')
        osm_elements = download_osm_rivers(AR_BBOX)
        print(f'\nTotal ways OSM descargados: {len(osm_elements)}')
        osm_lines = osm_to_linestrings(osm_elements)
        print(f'LineStrings válidos: {len(osm_lines)}')
        headwaters = extract_osm_headwaters(osm_lines, hr_geoms)
        osm_features_raw = headwaters

    # ── Construir features GeoJSON ─────────────────────────────────────────
    print('\n── Construyendo features ──')
    all_features = []

    # HydroRIVERS
    for f in hr_features:
        all_features.append({
            'type': 'Feature',
            'properties': {
                'id': f['id'],
                's': f['s'],
                'q': f['q'],
                'name': f['name'],
            },
            'geometry': mapping(f['geometry']),
        })

    # OSM-only (headwaters)
    for i, f in enumerate(osm_features_raw):
        is_river = f.get('is_river', False)
        s_proxy  = 4 if is_river else 2
        length_deg = f['geometry'].length if hasattr(f['geometry'], 'length') \
                     else shape(f['geometry']).length
        length_km = length_deg * 111
        q_proxy = max(1, min(15, round(length_km * 0.3)))
        all_features.append({
            'type': 'Feature',
            'properties': {
                'id': OSM_ID_BASE + i,
                's': s_proxy,
                'q': q_proxy,
                'name': f.get('name', ''),
                'osm_only': True,
            },
            'geometry': mapping(f['geometry']) if not isinstance(f['geometry'], dict)
                        else f['geometry'],
        })

    # Cerrar gaps de endpoints
    print('\n── Cerrando gaps de endpoints ──')
    all_features = close_endpoint_gaps(all_features)

    # ── Escribir output ────────────────────────────────────────────────────
    geojson = {
        'type': 'FeatureCollection',
        'metadata': {
            'title': 'Ríos de Argentina — HydroRIVERS + OSM headwaters',
            'source_hr': 'HydroRIVERS v10 (HydroSHEDS, public domain)',
            'source_osm': 'OpenStreetMap (Overpass API)',
            'generated_by': 'build_rivers_geom.py',
            'total_features': len(all_features),
            'hr_count': len(hr_features),
            'osm_only_count': len(osm_features_raw),
        },
        'features': all_features,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_PATH, 'w') as f:
        json.dump(geojson, f, ensure_ascii=False, separators=(',', ':'))

    size_mb = os.path.getsize(OUT_PATH) / (1024 * 1024)
    print(f'\n✓ Escrito: {OUT_PATH}')
    print(f'  HydroRIVERS: {len(hr_features)}')
    print(f'  OSM-only:    {len(osm_features_raw)}')
    print(f'  Total:       {len(all_features)}')
    print(f'  Tamaño:      {size_mb:.1f} MB')
    print('\n⚠️  Acordate de reiniciar el backend después de regenerar:')
    print('  pkill -f "uvicorn main:app" && uvicorn main:app --port 8000')


if __name__ == '__main__':
    main()
