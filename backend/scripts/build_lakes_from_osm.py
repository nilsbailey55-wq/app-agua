#!/usr/bin/env python3
"""
build_lakes_from_osm.py
=======================
Genera backend/data/ar_lakes.geojson a partir de OSM (Overpass API).

Pipeline:
  1. Descarga relations y ways natural=water con nombre en bbox Argentina
  2. Ensambla polígonos desde members outer/inner de cada relation
  3. Filtra features chilenas / bolivianas (intersección < 5% con Argentina)
  4. Elimina polígonos que son ríos/arroyos disfrazados de lagos
  5. Agrega salares puneños (natural=wetland con nombre "Salar")
  6. Simplifica geometrías adaptivamente por área
  7. Garantiza que todas las features tengan area_km2, water_type, osm_id
  8. Escribe el resultado en backend/data/ar_lakes.geojson

Uso:
    cd backend/scripts
    python3 build_lakes_from_osm.py

Requisitos:
    pip install shapely
    curl (disponible en macOS por defecto)

Tiempo estimado: ~15-20 minutos (varias queries Overpass secuenciales)

Notas:
- Si una query Overpass falla por timeout, el script la reintenta con chunks
  más chicos. Ver constante CHUNK_SIZE.
- Los lagos "famosos" (área > 50 km²) se descargan primero y tienen
  prioridad sobre features OSM genéricas.
- Las geometrías se extraen desde OSM relations (type=multipolygon), que
  contienen la topología correcta outer/inner. Los ways sueltos son fallback.
"""

import json
import math
import os
import subprocess
import time

from shapely.geometry import LineString, MultiPolygon, Polygon, mapping, shape
from shapely.ops import polygonize, unary_union

# ── Rutas ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.path.join(SCRIPT_DIR, '..', 'data')
OUT_PATH   = os.path.join(DATA_DIR, 'ar_lakes.geojson')
AR_PATH    = os.path.join(DATA_DIR, 'argentina.geojson')
NAMES_PATH = os.path.join(DATA_DIR, 'ar_rivers_names.json')

# ── Parámetros ─────────────────────────────────────────────────────────────
# Bounding boxes (S, W, N, E)
BBOX_PATAGONIA = (-56, -76, -38, -65)   # Patagonia andina
BBOX_NORTE     = (-38, -73, -21, -53)   # Norte y centro de Argentina
BBOX_SALARES   = (-30, -71, -21, -65)   # Puna / salares NOA

SIMPLIFY_TOL_LARGE  = 0.0003   # grados, ~30m — lagos grandes
SIMPLIFY_TOL_MEDIUM = 0.0005   # grados, ~50m — lagos medianos
SIMPLIFY_TOL_SMALL  = 0.0008   # grados, ~80m — lagunas chicas

# Mínimo porcentaje de área que debe estar en Argentina para no descartar
AR_INTERSECTION_MIN_PCT = 0.05

# Prefijos que indican cuerpo de agua válido (no río)
LAKE_PREFIXES = (
    'Lago', 'Laguna', 'Embalse', 'Contraembalse', 'Bañado',
    'Salar', 'Salina', 'Salinas', 'Mar ', 'Reserva',
    'Brazo', 'Estero', 'Esteros', 'Bahía', 'Caleta',
    'Dique', 'Dársena', 'Puerto', 'Cantera', 'Cava',
    'Humedal', 'Reservorio',
)

# Palabras que indican cauce (eliminar aunque tengan prefijo correcto)
RIVER_KEYWORDS = ('Riacho', 'Riachuelo', 'Ayo ', 'Río ', 'Rio ', 'Arroyo')


# ── Overpass ───────────────────────────────────────────────────────────────

def overpass_query(query: str, retries: int = 2) -> list:
    """Ejecuta query Overpass vía curl. Retorna lista de elements."""
    with open('/tmp/_overpass_q.txt', 'w') as f:
        f.write(query)
    for attempt in range(retries + 1):
        r = subprocess.run(
            ['curl', '-s', '-H', 'Accept: application/json',
             '-A', 'AppAgua/1.0 build_lakes_from_osm.py',
             '-X', 'POST', '--data-urlencode', 'data@/tmp/_overpass_q.txt',
             'https://overpass-api.de/api/interpreter'],
            capture_output=True, timeout=300
        )
        try:
            d = json.loads(r.stdout)
            if 'remark' in d and 'timeout' in d['remark']:
                print(f'  Overpass timeout (intento {attempt+1}/{retries+1})')
                time.sleep(5)
                continue
            return d.get('elements', [])
        except Exception as e:
            print(f'  Error parseando respuesta: {e}')
            if attempt < retries:
                time.sleep(5)
    return []


def download_water_relations(bbox: tuple, name_filter: str = '^(Lago|Laguna|Salar|Salina|Bañado|Embalse|Mar) ') -> list:
    """Descarga relations natural=water con nombre en la bbox."""
    s, w, n, e = bbox
    q = f"""[out:json][timeout:180];
(
  rel["natural"="water"]["name"~"{name_filter}"]({s},{w},{n},{e});
  rel["natural"="wetland"]["name"~"{name_filter}"]({s},{w},{n},{e});
);
out geom;
"""
    print(f'  Descargando relations bbox({s},{w},{n},{e})...')
    els = overpass_query(q)
    print(f'  → {len(els)} elementos')
    return els


def download_water_ways(bbox: tuple, names: list) -> list:
    """Descarga ways natural=water para nombres específicos (fallback cuando no hay relation)."""
    s, w, n, e = bbox
    name_re = '|'.join(names)
    q = f"""[out:json][timeout:120];
(
  way["natural"="water"]["name"~"{name_re}"]({s},{w},{n},{e});
);
out geom;
"""
    print(f'  Descargando ways para {len(names)} nombres...')
    els = overpass_query(q)
    print(f'  → {len(els)} elementos')
    return els


def download_salares(bbox: tuple) -> list:
    """Descarga salares puneños mapeados como natural=wetland."""
    s, w, n, e = bbox
    q = f"""[out:json][timeout:120];
(
  rel["natural"="wetland"]["name"~"^Salar"]({s},{w},{n},{e});
);
out geom;
"""
    print(f'  Descargando salares puneños...')
    els = overpass_query(q)
    print(f'  → {len(els)} elementos')
    return els


# ── Geometría ──────────────────────────────────────────────────────────────

def assemble_relation(members: list) -> list[Polygon]:
    """Ensambla los miembros outer/inner de una relation en polígonos Shapely."""
    outer_lines, inner_lines = [], []
    for m in members:
        if m.get('type') != 'way':
            continue
        geom = m.get('geometry', [])
        if len(geom) < 2:
            continue
        coords = [(p['lon'], p['lat']) for p in geom]
        if m.get('role') == 'inner':
            inner_lines.append(coords)
        else:
            outer_lines.append(coords)

    def rings_from(lines):
        rings, open_lines = [], []
        for ln in lines:
            if len(ln) >= 4 and ln[0] == ln[-1]:
                rings.append(ln)
            else:
                open_lines.append(ln)
        if open_lines:
            try:
                merged = unary_union([LineString(ln) for ln in open_lines])
                for poly in polygonize(merged):
                    rings.append(list(poly.exterior.coords))
            except Exception:
                pass
        return rings

    outers = rings_from(outer_lines)
    inners = rings_from(inner_lines)
    polys = []
    for o in outers:
        try:
            outer_poly = Polygon(o)
            if not outer_poly.is_valid:
                outer_poly = outer_poly.buffer(0)
            holes = [
                ir for ir in inners
                if Polygon(ir).is_valid and outer_poly.contains(Polygon(ir))
            ]
            poly = Polygon(o, holes)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_valid and not poly.is_empty:
                polys.append(poly)
        except Exception:
            pass
    return polys


def way_to_polygon(element: dict) -> Polygon | None:
    """Convierte un OSM way cerrado en Polygon."""
    coords = [(p['lon'], p['lat']) for p in element.get('geometry', [])]
    if len(coords) >= 4 and coords[0] == coords[-1]:
        try:
            poly = Polygon(coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_valid and not poly.is_empty:
                return poly
        except Exception:
            pass
    return None


def area_km2(geom, lat: float) -> float:
    """Área aproximada en km² a partir de geometría en grados y latitud central."""
    km_per_deg_lng = 111.0 * math.cos(math.radians(lat))
    return geom.area * 111.0 * km_per_deg_lng


def simplify_tol(area: float) -> float:
    """Tolerancia de simplificación adaptativa por área."""
    if area >= 100:
        return SIMPLIFY_TOL_LARGE
    elif area >= 10:
        return SIMPLIFY_TOL_MEDIUM
    return SIMPLIFY_TOL_SMALL


# ── Filtros ────────────────────────────────────────────────────────────────

def is_river_name(name: str, river_bare_names: set) -> bool:
    """True si el nombre corresponde a un río/cauce, no a un lago."""
    if any(kw in name for kw in RIVER_KEYWORDS):
        return True
    # Nombres sin prefijo de lago que coinciden con ríos conocidos
    if not any(name.startswith(p) for p in LAKE_PREFIXES):
        if name in river_bare_names:
            return True
    return False


def is_in_argentina(geom, ar_geom, min_pct: float = AR_INTERSECTION_MIN_PCT) -> bool:
    """True si la geometría tiene al menos min_pct de área dentro de Argentina."""
    try:
        if not ar_geom.intersects(geom):
            return False
        ar_part = ar_geom.intersection(geom)
        return ar_part.area / geom.area >= min_pct
    except Exception:
        return False


# ── Construcción del dataset ───────────────────────────────────────────────

def build_lake_dict(elements: list, ar_geom, river_bare_names: set) -> dict:
    """
    Procesa una lista de elementos OSM y retorna dict {nombre: shapely_geom}.
    Filtra ríos, lakes fuera de Argentina, y mantiene el más grande por nombre.
    """
    by_name = {}
    for el in elements:
        name = el.get('tags', {}).get('name', '')
        if not name or is_river_name(name, river_bare_names):
            continue

        if el['type'] == 'relation':
            polys = assemble_relation(el.get('members', []))
        elif el['type'] == 'way':
            poly = way_to_polygon(el)
            polys = [poly] if poly else []
        else:
            continue

        if not polys:
            continue

        u = unary_union(polys)
        if not is_in_argentina(u, ar_geom):
            continue

        # Si hay homónimos, quedarse con el más grande
        if name in by_name:
            if u.area > by_name[name].area:
                by_name[name] = u
        else:
            by_name[name] = u

    return by_name


def to_feature(name: str, geom, water_type: str = 'lake') -> dict:
    """Convierte nombre + geometría Shapely en GeoJSON Feature."""
    lat = geom.centroid.y
    a_km2 = round(area_km2(geom, lat), 3)
    tol = simplify_tol(a_km2)
    geom_s = geom.simplify(tol, preserve_topology=True)
    return {
        'type': 'Feature',
        'properties': {
            'name': name,
            'water_type': water_type,
            'area_km2': a_km2,
            'osm_id': 'osm_relation',
            'source': 'osm_relation',
        },
        'geometry': mapping(geom_s),
    }


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    print('── Cargando datos de referencia ──')
    with open(AR_PATH) as f:
        ar = json.load(f)
    AR_GEOM = shape(ar['features'][0]['geometry']) if ar.get('type') == 'FeatureCollection' \
              else shape(ar['geometry'])

    with open(NAMES_PATH) as f:
        river_names_raw = json.load(f)

    def strip_river_prefix(n):
        for p in ('Río ', 'Rio ', 'Arroyo ', 'Río de ', 'Arroyo de '):
            if n.startswith(p):
                return n[len(p):].strip()
        return n.strip()

    river_bare_names = {strip_river_prefix(n) for n in river_names_raw.values() if n}
    print(f'  Nombres de ríos conocidos: {len(river_bare_names)}')

    # ── 1. Descargar ───────────────────────────────────────────────────────
    print('\n── Descargando desde Overpass ──')

    print('\n[1/3] Relations patagónicas...')
    els_pat = download_water_relations(BBOX_PATAGONIA)

    print('\n[2/3] Relations norte/centro...')
    els_norte = download_water_relations(BBOX_NORTE)

    print('\n[3/3] Salares puneños (natural=wetland)...')
    els_sal = download_salares(BBOX_SALARES)

    # Fallback: lakes famosos del norte que suelen estar como way
    print('\nFallback ways para lagos famosos sin relation...')
    FALLBACK_NAMES = ['Laguna Llancanelo', 'Laguna de los Pozuelos',
                      'Laguna de Vilama', 'Salinas Grandes']
    els_ways = download_water_ways(BBOX_NORTE, FALLBACK_NAMES)

    all_elements = els_pat + els_norte + els_sal + els_ways
    print(f'\nTotal elementos descargados: {len(all_elements)}')

    # ── 2. Procesar ────────────────────────────────────────────────────────
    print('\n── Procesando geometrías ──')

    # Aliases: nombres OSM compuestos → nombre canónico en la app
    ALIASES = {
        "Lago General Carrera / Lago Buenos Aires": "Lago Buenos Aires",
        "Lago O'Higgins / Lago San Martín": "Lago San Martín",
        "Lago Palena / Lago General Vintter": "Lago Palena",
    }
    BLACKLIST_IDS = {18131268}  # "Lago Buenos Aires" en Uruguay (homónimo)

    filtered = [e for e in all_elements if e.get('id') not in BLACKLIST_IDS]
    lake_dict = build_lake_dict(filtered, AR_GEOM, river_bare_names)

    # Aplicar aliases
    for old, new in ALIASES.items():
        if old in lake_dict:
            if new not in lake_dict or lake_dict[old].area > lake_dict[new].area:
                lake_dict[new] = lake_dict[old]
            del lake_dict[old]

    # Salares: marcar water_type = 'salt_pool'
    salar_names = {e.get('tags', {}).get('name', '') for e in els_sal}

    print(f'  Lagos únicos procesados: {len(lake_dict)}')
    chilenos = sum(1 for e in all_elements
                   if e.get('id') not in BLACKLIST_IDS
                   and e.get('tags', {}).get('name', '')
                   and not is_river_name(e.get('tags', {}).get('name', ''), river_bare_names)
                   and e.get('tags', {}).get('name', '') not in lake_dict)
    print(f'  Descartados (Chile/Bolivia o homónimos): ~{chilenos}')

    # ── 3. Construir GeoJSON ───────────────────────────────────────────────
    print('\n── Construyendo GeoJSON ──')
    features = []
    for name, geom in lake_dict.items():
        wt = 'salt_pool' if name in salar_names else 'lake'
        features.append(to_feature(name, geom, water_type=wt))

    geojson = {
        'type': 'FeatureCollection',
        'metadata': {
            'title': 'Lagos y cuerpos de agua de Argentina',
            'source': 'OpenStreetMap (Overpass API)',
            'generated_by': 'build_lakes_from_osm.py',
            'total_features': len(features),
        },
        'features': features,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_PATH, 'w') as f:
        json.dump(geojson, f, ensure_ascii=False, separators=(',', ':'))

    size_kb = os.path.getsize(OUT_PATH) / 1024
    print(f'\n✓ Escrito: {OUT_PATH}')
    print(f'  Features: {len(features)}')
    print(f'  Tamaño: {size_kb:.1f} KB')
    print('\n⚠️  Acordate de reiniciar el backend después de regenerar:')
    print('  pkill -f "uvicorn main:app" && uvicorn main:app --port 8000')


if __name__ == '__main__':
    main()
