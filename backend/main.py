"""
Agua Argentina — Backend API
FastAPI + Shapely (point-in-polygon locate)
Run: uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from shapely.geometry import shape, Point
import json
import os
import asyncio
import ssl
import urllib.request
import urllib.parse

# SSL: intentar cargar bundle de certificados del sistema o del framework de Python
_SSL_CTX = ssl.create_default_context()
for _cert_path in [
    "/etc/ssl/certs/ca-certificates.crt",      # Debian/Ubuntu/Railway
    "/etc/ssl/cert.pem",                        # Alpine/macOS (brew)
    "/Library/Frameworks/Python.framework/Versions/3.13/etc/openssl/cert.pem",  # macOS Python.org
    "/Library/Frameworks/Python.framework/Versions/3.12/etc/openssl/cert.pem",
    "/Library/Frameworks/Python.framework/Versions/3.11/etc/openssl/cert.pem",
]:
    if os.path.exists(_cert_path):
        try:
            _SSL_CTX.load_verify_locations(_cert_path)
        except Exception:
            pass
        break

# ── load data ──────────────────────────────────────────────────────────────
DATA_DIR  = os.path.join(os.path.dirname(__file__), "data")
DATA_PATH = os.path.join(DATA_DIR, "basins.json")
GEOM_PATH = os.path.join(DATA_DIR, "basin_geometries.json")  # HydroBASINS-derived
RIVERS_PATH   = os.path.join(DATA_DIR, "ar_rivers.geojson")     # Natural Earth
LAKES_PATH    = os.path.join(DATA_DIR, "ar_lakes.geojson")      # Natural Earth
WETLANDS_PATH = os.path.join(DATA_DIR, "ar_wetlands.geojson")   # OSM (named, ≥0.5 km²)
GLACIERS_PATH  = os.path.join(DATA_DIR, "ar_glaciers.geojson")   # IANIGLA Inv. Nac. ≥1 km²
GSTATS_PATH    = os.path.join(DATA_DIR, "basin_glacier_stats.json")  # IANIGLA per-basin agg.
DAMS_PATH      = os.path.join(DATA_DIR, "ar_dams.geojson")        # Hidroeléctricas curadas
PROTECTED_PATH = os.path.join(DATA_DIR, "ar_protected.geojson")   # APN curadas
CITIES_PATH    = os.path.join(DATA_DIR, "ar_cities.geojson")      # Localidades ≥100k (INDEC/OSM)
AQUIFERS_PATH  = os.path.join(DATA_DIR, "ar_aquifers.geojson")    # Acuíferos (aprox., SEGEMAR/INA/SAG-UNESCO)
RIVERS_GRAPH_PATH = os.path.join(DATA_DIR, "ar_rivers_graph.json")  # HydroRIVERS Strahler≥2 (grafo upstream)
RIVERS_GEOM_PATH  = os.path.join(DATA_DIR, "ar_rivers_geom.geojson")  # HydroRIVERS Strahler≥4 (geometría visible)
RIVERS_NAMES_PATH = os.path.join(DATA_DIR, "ar_rivers_names.json")     # HYRIV_ID → name (de NE + OSM)
LAKES_GRAPH_PATH  = os.path.join(DATA_DIR, "ar_lakes_graph.json")     # Lakes con afluentes/outflow precomputados
INDIGENOUS_PATH   = os.path.join(DATA_DIR, "ar_indigenous.geojson")  # Territorios indígenas con conflicto hídrico (curado)
RAMSAR_PATH       = os.path.join(DATA_DIR, "ar_ramsar.geojson")      # 23 Sitios Ramsar oficiales (Convención 1971)
FLOW_SERIES_PATH  = os.path.join(DATA_DIR, "ar_flow_series.json")   # Series históricas caudal/nivel por cuenca
PRECIP_GRID_PATH  = os.path.join(DATA_DIR, "ar_precip_grid.json")   # Grid ERA5 pre-computado (build_precip_grid.py)

with open(DATA_PATH, encoding="utf-8") as f:
    BASINS: list[dict] = json.load(f)

# Override approximate hand-drawn geometries with HydroBASINS Level-5 polygons
if os.path.exists(GEOM_PATH):
    with open(GEOM_PATH, encoding="utf-8") as f:
        ACCURATE_GEOMS: dict = json.load(f)
    for _b in BASINS:
        if _b["id"] in ACCURATE_GEOMS:
            _b["geometry"] = ACCURATE_GEOMS[_b["id"]]
            _b["geometry_source"] = "HydroBASINS Level 5 (HydroSHEDS, public domain)"
        else:
            _b["geometry_source"] = "Aproximación manual"

# Cache rivers and lakes data once
with open(RIVERS_PATH, encoding="utf-8") as f:
    RIVERS = json.load(f)
with open(LAKES_PATH, encoding="utf-8") as f:
    LAKES = json.load(f)
with open(WETLANDS_PATH, encoding="utf-8") as f:
    WETLANDS = json.load(f)
with open(GLACIERS_PATH, encoding="utf-8") as f:
    GLACIERS = json.load(f)
with open(GSTATS_PATH, encoding="utf-8") as f:
    GLACIER_STATS = json.load(f)
with open(DAMS_PATH, encoding="utf-8") as f:
    DAMS = json.load(f)
with open(PROTECTED_PATH, encoding="utf-8") as f:
    PROTECTED = json.load(f)
with open(CITIES_PATH, encoding="utf-8") as f:
    CITIES = json.load(f)
with open(AQUIFERS_PATH, encoding="utf-8") as f:
    AQUIFERS = json.load(f)
with open(RIVERS_GRAPH_PATH, encoding="utf-8") as f:
    _raw_graph = json.load(f)
    # Convert keys back to int for fast lookup
    RIVERS_GRAPH = {int(k): v for k, v in _raw_graph.items()}
    del _raw_graph
with open(RIVERS_GEOM_PATH, encoding="utf-8") as f:
    RIVERS_GEOM = json.load(f)
with open(RIVERS_NAMES_PATH, encoding="utf-8") as f:
    _raw_names = json.load(f)
    RIVERS_NAMES = {int(k): v for k, v in _raw_names.items()}
    del _raw_names
with open(LAKES_GRAPH_PATH, encoding="utf-8") as f:
    LAKES_GRAPH = json.load(f)
with open(INDIGENOUS_PATH, encoding="utf-8") as f:
    INDIGENOUS = json.load(f)
with open(RAMSAR_PATH, encoding="utf-8") as f:
    RAMSAR = json.load(f)
with open(FLOW_SERIES_PATH, encoding="utf-8") as f:
    FLOW_SERIES = json.load(f)

# Grid de precipitación ERA5 pre-computado (opcional — generado por build_precip_grid.py)
_PRECIP_GRID: dict = {}
if os.path.exists(PRECIP_GRID_PATH):
    with open(PRECIP_GRID_PATH, encoding="utf-8") as f:
        _raw_grid = json.load(f)
        _PRECIP_GRID = _raw_grid.get("cells", {})
    print(f"[precip grid] {len(_PRECIP_GRID)} celdas cargadas desde ar_precip_grid.json")
else:
    print("[precip grid] ar_precip_grid.json no encontrado — usando Open-Meteo on-demand")

# Aggregate indigenous territories per basin
_indig_by_basin = {}
for _f in INDIGENOUS["features"]:
    _bid = _f["properties"].get("basin_id")
    if not _bid: continue
    _indig_by_basin.setdefault(_bid, []).append(_f["properties"])
for _b in BASINS:
    if _b["id"] in _indig_by_basin:
        _b["indigenous_territories"] = {
            "count": len(_indig_by_basin[_b["id"]]),
            "list": _indig_by_basin[_b["id"]],
        }

# ── Build upstream graph and spatial index for trace queries ──
from shapely.geometry import shape as _shape, Point as _Point
from shapely.strtree import STRtree as _STRtree

# Reverse the directed graph: NEXT_DOWN → [upstream HYRIV_IDs]
UPSTREAM_OF: dict[int, list[int]] = {}
for _hid, _rec in RIVERS_GRAPH.items():
    _down = _rec.get("d", 0)
    if _down:
        UPSTREAM_OF.setdefault(_down, []).append(_hid)

# Spatial index: only Strahler >= 4 segments are queryable (visible-on-map)
_geom_lines = []
_geom_ids = []
for _f in RIVERS_GEOM["features"]:
    try:
        _geom_lines.append(_shape(_f["geometry"]))
        _geom_ids.append(_f["properties"]["id"])
    except Exception:
        pass
_RIVER_TREE = _STRtree(_geom_lines)

# Spatial index for IANIGLA glaciers (so we can identify headwater glacier origins)
_GLACIER_PTS = []
_GLACIER_PROPS = []
for _f in GLACIERS["features"]:
    coords = _f["geometry"]["coordinates"]
    _GLACIER_PTS.append(_Point(coords[0], coords[1]))
    _GLACIER_PROPS.append(_f["properties"])
_GLACIER_TREE = _STRtree(_GLACIER_PTS) if _GLACIER_PTS else None

# Spatial index of lakes for "click in lake" detection
_LAKE_POLYS = []
_LAKE_KEYS = []
for _lk_id, _lk_data in LAKES_GRAPH.items():
    try:
        _LAKE_POLYS.append(_shape(_lk_data["geometry"]).buffer(0))
        _LAKE_KEYS.append(_lk_id)
    except Exception:
        continue
_LAKE_TREE = _STRtree(_LAKE_POLYS) if _LAKE_POLYS else None

# Attach IANIGLA per-basin stats to basin metadata
for _b in BASINS:
    if _b["id"] in GLACIER_STATS:
        _b["iangla_stats"] = GLACIER_STATS[_b["id"]]

# Aggregate dams and protected areas per basin
_dams_by_basin = {}
for _f in DAMS["features"]:
    _bid = _f["properties"].get("basin_id")
    if not _bid: continue
    _dams_by_basin.setdefault(_bid, []).append(_f["properties"])
for _b in BASINS:
    if _b["id"] in _dams_by_basin:
        _list = sorted(_dams_by_basin[_b["id"]], key=lambda d: -d.get("mw", 0))
        _b["dams"] = {
            "count": len(_list),
            "total_mw": sum(d.get("mw", 0) for d in _list),
            "list": _list,
        }

_protected_by_basin = {}
for _f in PROTECTED["features"]:
    _bid = _f["properties"].get("basin_id")
    if not _bid: continue
    _protected_by_basin.setdefault(_bid, []).append(_f["properties"])
for _b in BASINS:
    if _b["id"] in _protected_by_basin:
        _list = sorted(_protected_by_basin[_b["id"]], key=lambda p: -p.get("area_km2", 0))
        _b["protected_areas"] = {
            "count": len(_list),
            "total_area_km2": sum(p.get("area_km2", 0) for p in _list),
            "unesco_count": sum(1 for p in _list if p.get("unesco")),
            "ramsar_count": sum(1 for p in _list if p.get("ramsar")),
            "list": _list,
        }

_cities_by_basin = {}
for _f in CITIES["features"]:
    _bid = _f["properties"].get("basin_id")
    if not _bid: continue
    _cities_by_basin.setdefault(_bid, []).append(_f["properties"])
for _b in BASINS:
    if _b["id"] in _cities_by_basin:
        _list = sorted(_cities_by_basin[_b["id"]], key=lambda c: -c.get("population", 0))
        _b["cities"] = {
            "count": len(_list),
            "total_population": sum(c.get("population", 0) for c in _list),
            "list": _list,
        }

# Pre-build shapely geometries for point-in-polygon
_SHAPES: list[tuple[object, str]] = []
for _b in BASINS:
    try:
        _SHAPES.append((shape(_b["geometry"]), _b["id"]))
    except Exception:
        pass

# ── app ────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Agua Argentina API",
    description="Cuencas hídricas y glaciares de Argentina — MVP nacional",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Disable browser caching for the geo endpoints — we regenerate them
# while developing and need clients to fetch fresh data on each reload.
@app.middleware("http")
async def no_cache_middleware(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ── routes ─────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "project": "Agua Argentina",
        "version": "0.2.0",
        "endpoints": ["/api/basins", "/api/basins/{id}", "/api/locate", "/api/summary"],
        "docs": "/docs",
    }


@app.get("/api/basins")
def get_basins():
    """
    GeoJSON FeatureCollection with all basins.
    Geometry included for rendering; heavy detail fields omitted.
    """
    features = []
    for b in BASINS:
        features.append({
            "type": "Feature",
            "id": b["id"],
            "properties": {
                "id": b["id"],
                "name": b["name"],
                "region": b["region"],
                "area_km2": b["area_km2"],
                "status_overall": b["status"]["overall"],
            },
            "geometry": b["geometry"],
        })
    return {"type": "FeatureCollection", "features": features}


@app.get("/api/basins/{basin_id}")
def get_basin(basin_id: str):
    """Full detail for a single basin (status, glaciers, facts, sources)."""
    for b in BASINS:
        if b["id"] == basin_id:
            return b
    raise HTTPException(status_code=404, detail=f"Basin '{basin_id}' not found")


@app.get("/api/locate")
def locate(
    lat: float = Query(..., description="Latitude (decimal, negative = South)"),
    lng: float = Query(..., description="Longitude (decimal, negative = West)"),
):
    """
    Point-in-polygon lookup: returns the basin that contains the given coordinate.
    Useful for 'which basin is my location in?' flows.
    """
    pt = Point(lng, lat)
    for poly, basin_id in _SHAPES:
        if poly.contains(pt):
            for b in BASINS:
                if b["id"] == basin_id:
                    return {
                        "found": True,
                        "basin_id": b["id"],
                        "basin_name": b["name"],
                        "region": b["region"],
                        "status_overall": b["status"]["overall"],
                    }
    return {
        "found": False,
        "basin_id": None,
        "basin_name": None,
        "region": None,
        "status_overall": None,
        "message": "Coordenada fuera del área cubierta. Puede estar en el mar o en una zona sin shapefile cargado.",
    }


@app.get("/api/water/rivers")
def get_rivers():
    """Argentine rivers from Natural Earth 1:10m (public domain)."""
    return RIVERS


@app.get("/api/water/lakes")
def get_lakes():
    """Argentine lakes from Natural Earth 1:10m (public domain)."""
    return LAKES


@app.get("/api/dams")
def get_dams():
    """Hidroeléctricas y embalses ≥ 50 MW (lista curada de Wikipedia + CAMMESA + S. Energía)."""
    return DAMS


@app.get("/api/protected")
def get_protected():
    """Parques Nacionales y áreas protegidas (APN). Centroides + metadatos."""
    return PROTECTED


@app.get("/api/cities")
def get_cities():
    """Localidades argentinas ≥ 100.000 hab. (INDEC 2022 / OSM).
    Cada feature incluye población, prestadora de agua y fuente principal."""
    return CITIES


@app.get("/api/ice-fields")
def get_ice_fields():
    """Hielos continentales y glaciares andinos (Campo de Hielo Sur/Norte, Tronador, otros).
    Polígonos OSM agregados por región."""
    with open(os.path.join(DATA_DIR, "ar_ice_fields.geojson"), encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/citizen-conflicts")
def get_citizen_conflicts():
    """Asambleas y movimientos ciudadanos por agua (no indígenas).
    Casos paradigmáticos: Esquel, Famatina, Andalgalá, Chubutazo, Gualeguaychú, Atuel, Carlos Paz, etc."""
    with open(os.path.join(DATA_DIR, "ar_citizen_conflicts.geojson"), encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/ramsar")
def get_ramsar():
    """23 sitios RAMSAR oficiales de Argentina (Convención de Humedales 1971,
    Ley 23.919 + Ley 25.335). Cobertura: 5,6 M ha en 15 provincias + CABA.
    Algunos coinciden con Parques Nacionales (linked_to_apn=true)."""
    return RAMSAR


# ── precipitation cache (in-memory, keyed by 0.5° grid cell) ──────────────
_PRECIP_CACHE: dict = {}

_GRID_RES = 1.0   # debe coincidir con GRID_RES en build_precip_grid.py

def _grid_key(lat: float, lng: float) -> tuple:
    """Redondea a la celda de grilla más cercana para caching."""
    return (round(lat / _GRID_RES) * _GRID_RES, round(lng / _GRID_RES) * _GRID_RES)

def _grid_str_key(lat: float, lng: float) -> str:
    """Clave string para el grid pre-computado (formato del JSON)."""
    glat = round(round(lat / _GRID_RES) * _GRID_RES, 1)
    glng = round(round(lng / _GRID_RES) * _GRID_RES, 1)
    return f"{glat:.1f}_{glng:.1f}"

def _build_response_from_grid(cell: dict) -> dict:
    """Arma la respuesta estándar del endpoint a partir de una celda del grid."""
    return {
        "lat": cell["lat"],
        "lng": cell["lng"],
        "source": "grid",
        "metrics": [{
            "id":              "precip_annual",
            "label":           "Precipitación anual",
            "unit":            "mm/año",
            "historical_mean": cell["mean"],
            "alert_low":       None,
            "source":          "ERA5 reanalysis · Open-Meteo (pre-computado)",
            "note":            "Reanálisis ERA5 (~27 km). Media 1990-2025.",
            "data":            cell["data"],
        }],
    }

def _fetch_openmeteo(lat: float, lng: float) -> dict:
    """Llama a Open-Meteo ERA5 y retorna precipitación diaria 1990-2025."""
    params = urllib.parse.urlencode({
        "latitude":   round(lat, 4),
        "longitude":  round(lng, 4),
        "start_date": "1990-01-01",
        "end_date":   "2025-12-31",
        "daily":      "precipitation_sum",
        "timezone":   "UTC",
    })
    url = f"https://archive-api.open-meteo.com/v1/era5?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "AppAgua/1.0"})
    with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as r:
        return json.loads(r.read())

def _aggregate_annual(daily_data: dict) -> list[dict]:
    """Suma precipitación diaria → totales anuales."""
    times  = daily_data.get("time", [])
    values = daily_data.get("precipitation_sum", [])
    by_year: dict[int, list] = {}
    for t, v in zip(times, values):
        year = int(t[:4])
        if v is not None:
            by_year.setdefault(year, []).append(v)
    annual = []
    for year in sorted(by_year):
        vals = by_year[year]
        if len(vals) >= 300:         # descartar años con datos incompletos (<300 días)
            total = round(sum(vals), 1)
            annual.append({"year": year, "value": total})
    # Marcar el último dato como "actual"
    if annual:
        annual[-1]["is_current"] = True
    return annual


@app.get("/api/climate/precip")
async def get_climate_precip(
    lat: float = Query(..., description="Latitud decimal"),
    lng: float = Query(..., description="Longitud decimal"),
):
    """Precipitación anual histórica (1990-2025) para un punto geográfico.
    Usa grid pre-computado si está disponible; si no, llama a Open-Meteo on-demand.
    Cache en memoria por celda de 0.5° (~55 km)."""
    key     = _grid_key(lat, lng)
    str_key = _grid_str_key(lat, lng)

    if key not in _PRECIP_CACHE:
        # ── 1. intentar grid pre-computado ──────────────────────────────────
        if str_key in _PRECIP_GRID and _PRECIP_GRID[str_key] is not None:
            _PRECIP_CACHE[key] = _build_response_from_grid(_PRECIP_GRID[str_key])
        else:
            # ── 2. fallback: Open-Meteo on-demand ───────────────────────────
            try:
                raw = await asyncio.to_thread(_fetch_openmeteo, lat, lng)
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Error al consultar Open-Meteo: {e}")
            daily  = raw.get("daily", {})
            annual = _aggregate_annual(daily)
            if not annual:
                raise HTTPException(status_code=404, detail="Sin datos de precipitación para este punto.")
            values  = [d["value"] for d in annual]
            mean    = round(sum(values) / len(values), 1)
            _PRECIP_CACHE[key] = {
                "lat": raw.get("latitude", lat),
                "lng": raw.get("longitude", lng),
                "metrics": [{
                    "id":               "precip_annual",
                    "label":            "Precipitación anual",
                    "unit":             "mm/año",
                    "historical_mean":  mean,
                    "alert_low":        None,
                    "source":           "ERA5 reanalysis · Open-Meteo",
                    "note":             "Estimación por reanálisis (~27 km). Media calculada sobre el período 1990-2025.",
                    "data":             annual,
                }],
            }
    return _PRECIP_CACHE[key]


@app.get("/api/water/flow-series")
def get_flow_series(basin: str = Query(None, description="basin_id para filtrar (ej: negro_limay)")):
    """Series históricas de caudal/nivel por cuenca.
    Si no se especifica basin, retorna todas las series disponibles."""
    series = FLOW_SERIES.get("series", {})
    if basin:
        if basin not in series:
            raise HTTPException(status_code=404, detail=f"No hay series para la cuenca '{basin}'")
        return {
            "basin_id": basin,
            "metrics": series[basin]["metrics"],
            "metadata": FLOW_SERIES.get("metadata", {}),
        }
    # Retornar índice de cuencas disponibles (sin los datos completos)
    return {
        "available": [
            {"basin_id": k, "metrics": [m["id"] for m in v["metrics"]]}
            for k, v in series.items() if v.get("metrics")
        ],
        "metadata": FLOW_SERIES.get("metadata", {}),
    }


@app.get("/api/argentina-border")
def get_argentina_border():
    """Polígono fronterizo de Argentina (Natural Earth admin_0). Para overlay sutil."""
    with open(os.path.join(DATA_DIR, "argentina.geojson"), encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/aquifers")
def get_aquifers():
    """Acuíferos argentinos principales (aproximación cartográfica).
    Fuentes: SEGEMAR, INA, SAG-UNESCO, SAYTT-OEA, literatura académica.
    NOTA: Polígonos aproximados — la hidrogeología real es 3D."""
    return AQUIFERS


@app.get("/api/water/rivers_minor")
def get_rivers_minor():
    """Capa complementaria de ríos/arroyos nombrados de OSM (waterway=river|stream).
    Cubre cabeceras y arroyos chicos que HydroRIVERS Strahler≥4 no incluye.
    ~18k features, ~10 MB. Se carga lazily en el frontend (zoom ≥ 7)."""
    with open(os.path.join(DATA_DIR, "ar_rivers_minor.geojson"), encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/water/rivers_graph_geom")
def get_rivers_graph_geom():
    """HydroRIVERS Strahler≥4 simplificado para visualización.
    No incluido por default en la capa principal — solo si se quiere mostrar todo el grafo."""
    return RIVERS_GEOM


@app.get("/api/indigenous")
def get_indigenous():
    """Territorios de pueblos originarios con conflictos hídricos documentados.
    Dataset CURADO (no exhaustivo). Coordenadas son centroides regionales aproximados —
    NUNCA ubicaciones exactas de núcleos comunitarios. Práctica estándar en
    cartografía de derechos indígenas (igual criterio que Amnistía Internacional)."""
    return INDIGENOUS


@app.get("/api/water/origin")
def water_origin(
    lat: float = Query(..., description="Latitude (decimal, negative=South)"),
    lng: float = Query(..., description="Longitude (decimal, negative=West)"),
    max_radius_km: float = Query(15, description="Max distance to nearest river segment"),
    glacier_radius_km: float = Query(10, description="How close a glacier must be to a headwater to count as origin"),
):
    """
    Dado un punto, traza upstream el sistema fluvial y devuelve el "origen del agua".

    Returns:
      - found: bool — si encontramos un río cercano
      - segment: el HYRIV_ID inicial
      - chain: lista ordenada (downstream → upstream) de tramos representativos
      - headwaters: segmentos cabecera (sin upstream en el grafo)
      - glaciers_at_source: glaciares IANIGLA cerca de las cabeceras
      - basin_id: cuenca a la que pertenece
    """
    pt = _Point(lng, lat)

    # 0) Check if the click is INSIDE a known lake — handle specially
    if _LAKE_TREE:
        for poly_idx in _LAKE_TREE.query(pt):
            poly = _LAKE_POLYS[poly_idx]
            if not poly.contains(pt): continue
            lake_key = _LAKE_KEYS[poly_idx]
            lake_data = LAKES_GRAPH[lake_key]
            # Pick the "main affluent" (highest discharge) for upstream tracing
            affluents = lake_data.get("affluents", [])
            outflow = lake_data.get("outflow")

            # If no outflow detected but we have affluents, the largest-discharge
            # "affluent" is likely the outflow (it accumulates all the water)
            inferred_outflow = None
            if not outflow and affluents:
                top = affluents[0]
                second = affluents[1] if len(affluents) > 1 else None
                if second is None or (top.get("q", 0) > 1.5 * (second.get("q", 0) or 0.1)):
                    inferred_outflow = top

            # Trace upstream from the SECOND-largest affluent if first was reclassified as outflow
            trace_from = None
            if affluents:
                trace_from = affluents[1] if inferred_outflow else affluents[0]

            chain_result = None
            glaciers_at_source = []
            if trace_from:
                tr_id = trace_from["id"]
                visited = set()
                headwaters_for_lake = []

                def _trace_lake(hid: int, depth: int = 0, max_depth: int = 200):
                    if hid in visited or depth > max_depth: return
                    visited.add(hid)
                    rec = RIVERS_GRAPH.get(hid)
                    if not rec: return
                    upstream = UPSTREAM_OF.get(hid, [])
                    if not upstream:
                        headwaters_for_lake.append({"id": hid, **rec, "depth": depth, "name": RIVERS_NAMES.get(hid)})
                        return
                    for up_id in upstream:
                        _trace_lake(up_id, depth + 1, max_depth)

                _trace_lake(tr_id)

                # Glacier matching
                if _GLACIER_TREE and headwaters_for_lake:
                    for hw in headwaters_for_lake[:20]:
                        hw_pt = _Point(hw["lng"], hw["lat"])
                        try:
                            near_idxs = _GLACIER_TREE.query(hw_pt.buffer(glacier_radius_km / 111))
                        except Exception:
                            continue
                        for gi in near_idxs:
                            d = _GLACIER_PTS[gi].distance(hw_pt) * 111
                            if d <= glacier_radius_km:
                                glaciers_at_source.append({
                                    **_GLACIER_PROPS[gi],
                                    "distance_to_headwater_km": round(d, 1),
                                })
                    seen_names = set()
                    deduped = []
                    glaciers_at_source.sort(key=lambda g: g["distance_to_headwater_km"])
                    for g in glaciers_at_source:
                        key = (g.get("name"), g.get("tipo"))
                        if key in seen_names: continue
                        seen_names.add(key)
                        deduped.append(g)
                    glaciers_at_source = deduped[:15]

                chain_result = {
                    "headwaters_total": len(headwaters_for_lake),
                    "upstream_segments_total": len(visited),
                    "headwaters": headwaters_for_lake[:30],
                }

            # Find the basin
            basin_id = None
            for poly_b, bid in _SHAPES:
                if poly_b.contains(pt):
                    basin_id = bid
                    break

            return {
                "found": True,
                "is_lake": True,
                "lake": {
                    "name": lake_data.get("name"),
                    "affluents": affluents,
                    "affluents_total": lake_data.get("affluents_total", len(affluents)),
                    "outflow": outflow or inferred_outflow,
                    "outflow_inferred": bool(inferred_outflow),
                },
                "query": {"lat": lat, "lng": lng},
                "main_chain": [],   # not applicable for lakes (multiple sources)
                "headwaters": chain_result["headwaters"] if chain_result else [],
                "headwaters_total": chain_result["headwaters_total"] if chain_result else 0,
                "upstream_segments_total": chain_result["upstream_segments_total"] if chain_result else 0,
                "glaciers_at_source": glaciers_at_source,
                "basin_id": basin_id,
                "main_river_name": (outflow or inferred_outflow or {}).get("name") if (outflow or inferred_outflow) else None,
            }

    # 1) Find nearest river segment (Strahler >= 4)
    if not _RIVER_TREE or not _geom_lines:
        return {"found": False, "message": "No hay grafo de ríos cargado"}

    # STRtree.nearest returns the index of the geometry in the input list
    nearest_idx = _RIVER_TREE.nearest(pt)
    if nearest_idx is None:
        return {"found": False, "message": "No se encontró río cercano"}

    nearest_line = _geom_lines[nearest_idx]
    nearest_id = _geom_ids[nearest_idx]
    distance_deg = nearest_line.distance(pt)
    distance_km = distance_deg * 111  # rough deg→km

    if distance_km > max_radius_km:
        return {
            "found": False,
            "distance_km": round(distance_km, 1),
            "message": f"Río más cercano a {distance_km:.1f} km — fuera de tolerancia ({max_radius_km} km)",
        }

    # 2) Trace upstream from nearest segment
    visited = set()
    headwaters = []
    chain_summary = []  # representative segments along the trace

    def trace(hid: int, depth: int = 0, max_depth: int = 200):
        if hid in visited or depth > max_depth: return
        visited.add(hid)
        rec = RIVERS_GRAPH.get(hid)
        if not rec: return
        upstream = UPSTREAM_OF.get(hid, [])
        if not upstream:
            # Headwater segment — no upstream in our graph
            headwaters.append({"id": hid, **rec, "depth": depth})
            return
        # Recurse on all upstream branches
        for up_id in upstream:
            trace(up_id, depth + 1, max_depth)

    trace(nearest_id)

    # 3) Build a representative "chain" by following the dominant upstream
    # (highest Strahler, then highest discharge) at each branch point
    def follow_main_branch(hid: int, max_steps: int = 50):
        steps = []
        seen = {hid}
        current = hid
        for _ in range(max_steps):
            rec = RIVERS_GRAPH.get(current)
            if not rec: break
            steps.append({"id": current, **rec})
            ups = [u for u in UPSTREAM_OF.get(current, []) if u not in seen]
            if not ups: break
            # Pick the dominant upstream branch (highest discharge, ties broken by Strahler)
            ups.sort(key=lambda u: (RIVERS_GRAPH.get(u, {}).get("q", 0),
                                     RIVERS_GRAPH.get(u, {}).get("s", 0)), reverse=True)
            current = ups[0]
            seen.add(current)
        return steps

    main_chain = follow_main_branch(nearest_id)

    # Enrich with names where known
    for step in main_chain:
        step["name"] = RIVERS_NAMES.get(step["id"])
    for hw in headwaters:
        hw["name"] = RIVERS_NAMES.get(hw["id"])

    # Identify the "main river name" of the chain (most frequent name in chain)
    main_river_name = None
    if main_chain:
        from collections import Counter as _Counter
        name_counts = _Counter(s["name"] for s in main_chain if s.get("name"))
        if name_counts:
            main_river_name = name_counts.most_common(1)[0][0]

    # 4) Identify glaciers near headwaters
    glaciers_at_source = []
    if _GLACIER_TREE and headwaters:
        glacier_radius_deg = glacier_radius_km / 111
        for hw in headwaters[:20]:  # cap to avoid runaway
            hw_pt = _Point(hw["lng"], hw["lat"])
            # Find glaciers within radius
            try:
                near_idxs = _GLACIER_TREE.query(hw_pt.buffer(glacier_radius_deg))
            except Exception:
                near_idxs = []
            for gi in near_idxs:
                g_pt = _GLACIER_PTS[gi]
                d = g_pt.distance(hw_pt) * 111  # deg → km
                if d <= glacier_radius_km:
                    glaciers_at_source.append({
                        **_GLACIER_PROPS[gi],
                        "distance_to_headwater_km": round(d, 1),
                    })
        # Dedupe by name + tipo, keep closest
        seen_names = set()
        deduped = []
        glaciers_at_source.sort(key=lambda g: g["distance_to_headwater_km"])
        for g in glaciers_at_source:
            key = (g.get("name"), g.get("tipo"))
            if key in seen_names: continue
            seen_names.add(key)
            deduped.append(g)
        glaciers_at_source = deduped[:15]  # top 15 closest

    # 5) Identify basin the point falls into (re-using existing locate logic)
    basin_id = None
    for poly, bid in _SHAPES:
        if poly.contains(pt):
            basin_id = bid
            break

    # 6) Aggregate stats
    n_upstream = len(visited)
    starting_seg = RIVERS_GRAPH.get(nearest_id, {})

    return {
        "found": True,
        "query": {"lat": lat, "lng": lng},
        "distance_to_river_km": round(distance_km, 2),
        "starting_segment": {
            "id": nearest_id,
            "strahler": starting_seg.get("s"),
            "discharge_m3s": starting_seg.get("q"),
            "length_km": starting_seg.get("lk"),
            "endorheic": bool(starting_seg.get("e")),
            "lat": starting_seg.get("lat"),
            "lng": starting_seg.get("lng"),
            "name": RIVERS_NAMES.get(nearest_id),
        },
        "main_river_name": main_river_name,
        "main_chain": main_chain,            # upstream-following chain (dominant branch)
        "headwaters": headwaters[:30],       # all headwater segments (capped)
        "headwaters_total": len(headwaters),
        "upstream_segments_total": n_upstream,
        "glaciers_at_source": glaciers_at_source,
        "basin_id": basin_id,
    }


@app.get("/api/water/glaciers")
def get_glaciers():
    """Argentine glaciers ≥ 1 km² as points (centroides) from IANIGLA Inventario Nacional 2018.
    Each feature has area_km2, tipo (GD/GC/GEA/GEI/MN/GCGE) and is_major (≥5 km²)."""
    return GLACIERS


@app.get("/api/water/wetlands")
def get_wetlands():
    """Named wetlands ≥ 0.5 km² in Argentina, from OpenStreetMap.
    Includes mallines, bofedales, bañados, salares, esteros, etc.
    Each feature has wetland_type and is_mallin flag."""
    return WETLANDS


@app.get("/api/summary")
def summary():
    """Aggregate statistics across all basins."""
    total = len(BASINS)
    by_status = {"red": 0, "yellow": 0, "green": 0}
    total_area = 0
    glaciers_count = 0
    for b in BASINS:
        s = b["status"]["overall"]
        if s in by_status:
            by_status[s] += 1
        total_area += b.get("area_km2", 0)
        glaciers_count += len(b.get("glaciers", []))
    iangla_total_count = sum(s.get("count", 0) for s in GLACIER_STATS.values())
    iangla_total_area = sum(s.get("area_km2", 0) for s in GLACIER_STATS.values())
    return {
        "basins_total": total,
        "status_breakdown": by_status,
        "total_area_km2": total_area,
        "glaciers_indexed": glaciers_count,
        "wetlands_total": len(WETLANDS.get("features", [])),
        "rivers_total":   len(RIVERS.get("features", [])),
        "lakes_total":    len(LAKES.get("features", [])),
        "iangla_glaciers_total": iangla_total_count,
        "iangla_ice_area_km2":   round(iangla_total_area, 1),
        "iangla_visible_on_map": len(GLACIERS.get("features", [])),
        "dams_total":            len(DAMS.get("features", [])),
        "dams_total_mw":         sum(f["properties"].get("mw", 0) for f in DAMS.get("features", [])),
        "protected_total":       len(PROTECTED.get("features", [])),
        "protected_total_km2":   sum(f["properties"].get("area_km2", 0) for f in PROTECTED.get("features", [])),
        "cities_total":          len(CITIES.get("features", [])),
        "cities_total_population": sum(f["properties"].get("population", 0) for f in CITIES.get("features", [])),
        "aquifers_total":        len(AQUIFERS.get("features", [])),
        "indigenous_territories_total": len(INDIGENOUS.get("features", [])),
        "ramsar_sites_total": len(RAMSAR.get("features", [])),
        "ramsar_total_ha": sum(f["properties"].get("area_ha", 0) for f in RAMSAR.get("features", [])),
        "coverage": "Cuencas HydroBASINS L5 · Ríos HydroRIVERS · Hidrografía NE · Humedales OSM · Glaciares IANIGLA · Represas CAMMESA · Áreas APN · Ciudades INDEC · Acuíferos SEGEMAR/INA/SAG · Territorios indígenas (curado, INAI/Amnistía)",
    }


# ── TOOLS ─────────────────────────────────────────────────────────────────────

def _fetch_elevations(points: list[tuple[float, float]]) -> list[float | None]:
    """Fetch elevation (m) for a list of (lat, lng) via OpenTopoData SRTM30m.
    Returns list of floats (None if missing). Free, no key, max 100 pts/request."""
    loc_str = "|".join(f"{lat},{lng}" for lat, lng in points)
    params = urllib.parse.urlencode({"locations": loc_str})
    url = f"https://api.opentopodata.org/v1/srtm30m?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "AppAgua/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as r:
            data = json.loads(r.read())
        results = data.get("results", [])
        return [r.get("elevation") for r in results]
    except Exception as e:
        print(f"[elevation] error: {e}")
        return [None] * len(points)


@app.get("/api/tools/flood")
def flood_susceptibility(
    lat: float = Query(..., description="Latitud decimal (negativa = Sur)"),
    lng: float = Query(..., description="Longitud decimal (negativa = Oeste)"),
):
    """
    Herramienta de susceptibilidad a inundación para un punto dado.
    Combina:
      - Proximidad al río más cercano (STRtree sobre HydroRIVERS Strahler≥4)
      - Orden de Strahler / caudal estimado del río
      - Posición topográfica relativa (SRTM30m vía OpenTopoData)
      - Pendiente local (~1 km radio)
    Devuelve nivel de riesgo, puntaje 0-100 y factores explicativos.
    """
    import math

    pt = Point(lng, lat)

    # ── 1) River proximity ──────────────────────────────────────────────────
    # Search nearest river segment within 0.5° (~55 km)
    SEARCH_DEG = 0.5
    search_buf = pt.buffer(SEARCH_DEG)
    candidates = _RIVER_TREE.query(search_buf)

    river_dist_km = None
    nearest_strahler = 0
    nearest_discharge = 0.0
    nearest_river_name = None

    if len(candidates):
        best_dist = float("inf")
        best_idx = None
        for idx in candidates:
            seg_geom = _geom_lines[idx]
            d = pt.distance(seg_geom)
            if d < best_dist:
                best_dist = d
                best_idx = idx

        if best_idx is not None:
            # degrees → km at Argentina's latitude (~38°S): 1° ≈ 111 km lat, 88 km lon
            lat_rad = abs(lat) * math.pi / 180
            km_per_deg = 111.0 * math.cos(lat_rad)
            river_dist_km = round(best_dist * km_per_deg, 2)

            seg_props = RIVERS_GEOM["features"][best_idx]["properties"]
            nearest_strahler  = seg_props.get("s", 0)
            nearest_discharge = seg_props.get("q", 0.0) or 0.0
            nearest_river_name = seg_props.get("name")
            if not nearest_river_name:
                nearest_river_name = RIVERS_NAMES.get(_geom_ids[best_idx])

    # ── 2) Elevation sampling ───────────────────────────────────────────────
    # Center + 4 compass points at ~1 km offset
    D_LAT = 0.009   # ~1 km northward
    D_LNG = 0.011   # ~1 km eastward at 38°S
    elev_pts = [
        (lat,         lng),          # center
        (lat + D_LAT, lng),          # N
        (lat - D_LAT, lng),          # S
        (lat,         lng + D_LNG),  # E
        (lat,         lng - D_LNG),  # W
    ]
    elevations = _fetch_elevations(elev_pts)
    elev_center   = elevations[0]
    elev_surround = [e for e in elevations[1:] if e is not None]

    # ── 3) Scoring ──────────────────────────────────────────────────────────
    score = 0
    factors = []

    # 3a) River distance (0–45 pts)
    if river_dist_km is not None:
        if river_dist_km < 0.2:
            pts_dist = 45
        elif river_dist_km < 0.5:
            pts_dist = 35
        elif river_dist_km < 1.0:
            pts_dist = 25
        elif river_dist_km < 2.0:
            pts_dist = 15
        elif river_dist_km < 5.0:
            pts_dist = 7
        else:
            pts_dist = 0
        score += pts_dist

        river_label = nearest_river_name or "río sin nombre"
        dist_str = f"{river_dist_km:.1f} km" if river_dist_km >= 0.1 else f"{river_dist_km*1000:.0f} m"
        factors.append({
            "id":    "river_dist",
            "label": "Distancia al río más cercano",
            "value": dist_str,
            "detail": f"{river_label} (Strahler {nearest_strahler})",
            "pts":   pts_dist,
            "max":   45,
        })
    else:
        factors.append({
            "id": "river_dist", "label": "Distancia al río más cercano",
            "value": "> 55 km", "detail": "Sin ríos principales en radio de análisis",
            "pts": 0, "max": 45,
        })

    # 3b) River magnitude — Strahler order (0–15 pts)
    if nearest_strahler and river_dist_km is not None and river_dist_km < 10:
        if nearest_strahler >= 9:
            pts_mag = 15
        elif nearest_strahler >= 7:
            pts_mag = 12
        elif nearest_strahler >= 5:
            pts_mag = 8
        elif nearest_strahler >= 3:
            pts_mag = 4
        else:
            pts_mag = 1
        score += pts_mag
        factors.append({
            "id":    "river_mag",
            "label": "Magnitud del río",
            "value": f"Orden {nearest_strahler}",
            "detail": f"Caudal estimado ≈ {nearest_discharge:.0f} m³/s" if nearest_discharge else "caudal no disponible",
            "pts":   pts_mag,
            "max":   15,
        })
    else:
        factors.append({
            "id": "river_mag", "label": "Magnitud del río",
            "value": "—", "detail": "Sin dato cercano",
            "pts": 0, "max": 15,
        })

    # 3c) Topographic position (0–25 pts)
    if elev_center is not None and len(elev_surround) >= 2:
        mean_surround = sum(elev_surround) / len(elev_surround)
        diff = mean_surround - elev_center   # positive = center is lower (depression)
        if diff > 20:
            pts_topo = 25
        elif diff > 10:
            pts_topo = 20
        elif diff > 5:
            pts_topo = 15
        elif diff > 2:
            pts_topo = 10
        elif diff > 0:
            pts_topo = 5
        else:
            pts_topo = 0  # higher or same as surroundings → low flood risk
        score += pts_topo
        sign = "+" if diff >= 0 else ""
        factors.append({
            "id":    "topo_pos",
            "label": "Posición topográfica",
            "value": f"{elev_center:.0f} m s.n.m.",
            "detail": f"Entorno promedio: {mean_surround:.0f} m (diferencia: {sign}{diff:.1f} m)",
            "pts":   pts_topo,
            "max":   25,
        })
    else:
        factors.append({
            "id": "topo_pos", "label": "Posición topográfica",
            "value": "No disponible", "detail": "No se pudo obtener altimetría SRTM",
            "pts": 0, "max": 25,
        })

    # 3d) Local slope (0–15 pts)
    if elev_center is not None and len(elev_surround) >= 2:
        # Max elevation diff among surrounding pts / distance (~1 km)
        diffs = [abs(e - elev_center) for e in elev_surround]
        max_diff = max(diffs)
        slope_pct = (max_diff / 1000) * 100   # rise/run × 100 (1 km run)
        if slope_pct < 0.5:
            pts_slope = 15
        elif slope_pct < 1.0:
            pts_slope = 12
        elif slope_pct < 2.0:
            pts_slope = 8
        elif slope_pct < 5.0:
            pts_slope = 4
        else:
            pts_slope = 0
        score += pts_slope
        slope_desc = (
            "Muy llano" if slope_pct < 0.5 else
            "Llano"     if slope_pct < 1.0 else
            "Suave"     if slope_pct < 2.0 else
            "Moderado"  if slope_pct < 5.0 else "Pronunciado"
        )
        factors.append({
            "id":    "slope",
            "label": "Pendiente local (~1 km)",
            "value": f"{slope_pct:.1f}%",
            "detail": slope_desc + " — " + (
                "el agua puede acumularse" if slope_pct < 1.0 else
                "drenaje moderado"         if slope_pct < 5.0 else "buen drenaje"
            ),
            "pts":   pts_slope,
            "max":   15,
        })
    else:
        factors.append({
            "id": "slope", "label": "Pendiente local",
            "value": "No disponible", "detail": "",
            "pts": 0, "max": 15,
        })

    # ── 4) Risk level ───────────────────────────────────────────────────────
    score = min(score, 100)
    if score >= 76:
        risk_level = "Muy Alto"
        risk_color = "red"
        risk_desc  = "Alta probabilidad de anegamiento ante lluvias intensas o crecida fluvial."
    elif score >= 51:
        risk_level = "Alto"
        risk_color = "orange"
        risk_desc  = "Zona con condiciones favorables para inundaciones. Evitar construcciones en áreas bajas."
    elif score >= 26:
        risk_level = "Moderado"
        risk_color = "yellow"
        risk_desc  = "Riesgo presente, especialmente en eventos de precipitación extrema."
    else:
        risk_level = "Bajo"
        risk_color = "green"
        risk_desc  = "Condiciones relativamente favorables. El riesgo cero no existe."

    return {
        "query":       {"lat": lat, "lng": lng},
        "risk_level":  risk_level,
        "risk_color":  risk_color,
        "risk_desc":   risk_desc,
        "score":       score,
        "score_max":   100,
        "factors":     factors,
        "elevation_m": round(elev_center, 1) if elev_center is not None else None,
        "note":        "Análisis indicativo basado en SRTM30m + HydroRIVERS. No reemplaza estudios hidráulicos profesionales.",
        "sources":     ["HydroRIVERS (HydroSHEDS)", "SRTM30m vía OpenTopoData"],
    }
