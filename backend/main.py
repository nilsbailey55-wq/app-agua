"""
Agua Argentina — Backend API
FastAPI + Shapely (point-in-polygon locate)
Run: uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from shapely.geometry import shape, Point
from shapely.ops import nearest_points as _shp_nearest_points
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
WATER_BODY_PATH   = os.path.join(DATA_DIR, "water_body_area.json")  # Monitoreo superficie cuerpos de agua (Landsat/GSW-JRC)
CHIRPS_PATH       = os.path.join(DATA_DIR, "chirps_basin_precip.json")  # CHIRPS v2.0 precipitación anual por cuenca 1981-2024
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
with open(WATER_BODY_PATH, encoding="utf-8") as f:
    WATER_BODIES = json.load(f)
with open(CHIRPS_PATH, encoding="utf-8") as f:
    CHIRPS = json.load(f)

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

# Spatial lookup for aquifer polygons (only 8, no STRtree needed)
_AQUIFER_SHAPES: list[tuple[object, dict]] = []
for _f in AQUIFERS["features"]:
    try:
        _AQUIFER_SHAPES.append((shape(_f["geometry"]).buffer(0), _f["properties"]))
    except Exception:
        pass

# Flat city list for proximity checks in tool endpoints
_CITY_COORDS: list[tuple[float, float, int, str]] = []  # (lat, lng, pop, name)
for _f in CITIES["features"]:
    _coords = _f["geometry"]["coordinates"]
    _props  = _f["properties"]
    _CITY_COORDS.append((_coords[1], _coords[0], _props.get("population", 0), _props.get("name", "")))

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
    result = _PRECIP_CACHE[key]
    # Enrich with anomaly stats (applied after caching to avoid storing duplicated data)
    return {
        **result,
        "metrics": [_flow_series_stats(m) for m in result["metrics"]],
    }


def _flow_series_stats(metric: dict) -> dict:
    """Compute anomaly statistics for a flow metric and return enriched copy."""
    import math, copy
    m = copy.deepcopy(metric)
    data = m.get("data", [])
    if len(data) < 3:
        return m

    values = [d["value"] for d in data]
    mean   = m.get("historical_mean") or (sum(values) / len(values))

    # Population std-dev using all data points
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std_dev  = math.sqrt(variance)

    sigma_bands = {
        "mean":        round(mean, 2),
        "std_dev":     round(std_dev, 2),
        "band_1s_lo":  round(mean - std_dev,       2),
        "band_1s_hi":  round(mean + std_dev,       2),
        "band_15s_lo": round(mean - 1.5 * std_dev, 2),
        "band_15s_hi": round(mean + 1.5 * std_dev, 2),
        "band_2s_lo":  round(mean - 2.0 * std_dev, 2),
        "band_2s_hi":  round(mean + 2.0 * std_dev, 2),
    }

    # Classify current value
    current_pt = next((d for d in reversed(data) if d.get("is_current")), None)
    anomaly = None
    if current_pt is not None:
        v   = current_pt["value"]
        z   = (v - mean) / std_dev if std_dev > 0 else 0
        pct = round((v / mean - 1) * 100, 1) if mean else 0

        if   z <= -2.5:  status, color = "Sequía severa",     "red"
        elif z <= -1.5:  status, color = "Déficit hídrico",   "orange"
        elif z <= -0.5:  status, color = "Levemente bajo",    "yellow"
        elif z >=  2.5:  status, color = "Crecida severa",    "red"
        elif z >=  1.5:  status, color = "Caudal elevado",    "blue"
        elif z >=  0.5:  status, color = "Levemente alto",    "yellow"
        else:            status, color = "Normal",             "green"

        anomaly = {
            "value":    v,
            "z_score":  round(z, 2),
            "pct_vs_mean": pct,
            "status":   status,
            "color":    color,
            "label":    current_pt.get("label", str(current_pt.get("year"))),
        }

    m["sigma_bands"] = sigma_bands
    m["anomaly"]     = anomaly
    return m


@app.get("/api/water/flow-series")
def get_flow_series(basin: str = Query(None, description="basin_id para filtrar (ej: negro_limay)")):
    """Series históricas de caudal/nivel por cuenca, enriquecidas con estadísticas
    de anomalía: desviación estándar, bandas ±1σ/±1.5σ/±2σ y clasificación
    del valor actual respecto a la media histórica.
    Si no se especifica basin, retorna el índice de cuencas disponibles."""
    series = FLOW_SERIES.get("series", {})
    if basin:
        if basin not in series:
            raise HTTPException(status_code=404, detail=f"No hay series para la cuenca '{basin}'")
        enriched_metrics = [_flow_series_stats(m) for m in series[basin]["metrics"]]
        return {
            "basin_id": basin,
            "metrics":  enriched_metrics,
            "metadata": FLOW_SERIES.get("metadata", {}),
        }
    return {
        "available": [
            {"basin_id": k, "metrics": [m["id"] for m in v["metrics"]]}
            for k, v in series.items() if v.get("metrics")
        ],
        "metadata": FLOW_SERIES.get("metadata", {}),
    }


# ── Water Quality Index model ──────────────────────────────────────────────
# Base pressure score per basin (0–100, higher = better baseline quality).
# Derived from curated expert assessment in basins.json (calidad field) +
# land use context. These are stable anchors; flow anomaly shifts them.
_WQ_BASE: dict[str, int] = {
    "rio_de_la_plata":     14,   # crítica: AMBA + Riachuelo + agroquímicos
    "alto_parana":         35,   # deficiente: deforestación + agro intensivo
    "sierras_pampeanas":   38,   # deficiente: Suquía/Primero + Córdoba urbana
    "salado_bonaerense":   46,   # regular: agroquímicos + mal drenaje
    "paraguay_pilcomayo":  42,   # regular: ganadería + extracción petróleo Chaco
    "bermejo":             50,   # regular: erosión severa + sedimentos
    "salado_norte":        53,   # regular: agro + ciudades medias
    "cuyo":                52,   # regular: riego intensivo + salinización
    "colorado":            64,   # aceptable: tensión agro pero dilución OK
    "uruguay":             62,   # aceptable: algo de agro/papel
    "negro_limay":         67,   # aceptable: central hidroeléctrica regula
    "chubut":              71,   # buena: baja densidad urbana/agro
    "puna":                68,   # buena pero minería: litio + boratos
    "santa_cruz":          80,   # buena: casi sin presión antrópica
    "deseado":             74,   # buena: escasa actividad, ría protegida
    "gallegos":            76,   # buena: ganadería extensiva baja carga
    "tierra_del_fuego":    70,   # buena pero castores + salmonicultura
}

# Pressure labels for frontend (what drives quality pressure in this basin)
_WQ_PRESSURE: dict[str, list[str]] = {
    "rio_de_la_plata":    ["Efluentes AMBA (12M hab.)", "Riachuelo / ACUMAR", "Agroquímicos deltaicos"],
    "alto_parana":        ["Deforestación cuenca alta", "Soja + agroquímicos", "Presa Yacyretá (cianobact.)"],
    "sierras_pampeanas":  ["Efluentes Córdoba urbana", "Río Suquía contaminado", "Agroquímicos pampeanos"],
    "salado_bonaerense":  ["Agroquímicos bonaerenses", "Feedlots ganaderos", "Drenaje lento / acumulación"],
    "paraguay_pilcomayo": ["Ganadería extensiva Chaco", "Petróleo Formosa/Salta", "Sedimentos Pilcomayo"],
    "bermejo":            ["Erosión severa (turbidez)", "Deforestación NOA/Bolivia", "Agro subtropical"],
    "salado_norte":       ["Ciudades medias (Salta/Jujuy)", "Agricultura cañera", "Embalse Cabra Corral"],
    "cuyo":               ["Riego intensivo vitivinícola", "Salinización progresiva", "Aguas residuales Mendoza"],
    "colorado":           ["Riego agrícola alto/bajo", "Salinización deltaica", "Extracción petróleo Neuquén"],
    "uruguay":            ["Papeleras (Fray Bentos)", "Agro sojero litoral", "Efluentes Concordia/Salto"],
    "negro_limay":        ["Regulación hidroeléctrica", "Fruticultura Alto Valle", "Residuos urbanos Neuquén"],
    "chubut":             ["Extracción petróleo cuenca", "Ciudad de Trelew efluentes", "Pesca en ría"],
    "puna":               ["Minería litio/boratos", "Salinización natural alta", "Sin tratamiento cloacal"],
    "santa_cruz":         ["Mínima presión antrópica", "Turismo Calafate creciente", "Sedimentos glaciarios"],
    "deseado":            ["Efímero: mínima dilución", "Puerto Deseado efluentes", "Acuífero vulnerable"],
    "gallegos":           ["Ganadería ovina extensiva", "Ciudad Río Gallegos", "Turba/peatlands"],
    "tierra_del_fuego":   ["Invasión castores (300+ ríos)", "Salmonicultura tramos medios", "Sin tratamiento rural"],
}

def _compute_wqi(basin_id: str) -> dict | None:
    """Computa el Water Quality Index dinámico para una cuenca.
    Combina score base (presión antrópica estática) con modificador de
    caudal actual (efecto de dilución/escorrentía de contaminantes)."""
    import math

    base = _WQ_BASE.get(basin_id)
    if base is None:
        return None

    # Get current flow anomaly from FLOW_SERIES
    flow_z: float | None = None
    flow_label: str | None = None
    flow_pct: float | None = None
    series = FLOW_SERIES.get("series", {}).get(basin_id)
    if series and series.get("metrics"):
        m = series["metrics"][0]
        enriched = _flow_series_stats(m)
        an = enriched.get("anomaly")
        if an:
            flow_z     = an["z_score"]
            flow_label = an["status"]
            flow_pct   = an["pct_vs_mean"]

    # Flow dilution modifier (scientific basis: dilution ∝ Q; pollutant
    # concentration ≈ load/Q, so low Q → worse quality)
    dilution_delta = 0
    dilution_note  = "Caudal en rango normal — dilución sin cambios respecto al promedio histórico"
    if flow_z is not None:
        if   flow_z <= -2.5:
            dilution_delta = -20
            dilution_note  = f"Sequía severa (z={flow_z:.1f}): dilución muy reducida, concentración de contaminantes aumenta fuertemente"
        elif flow_z <= -1.5:
            dilution_delta = -12
            dilution_note  = f"Déficit hídrico (z={flow_z:.1f}): menor dilución, calidad degradada respecto al promedio"
        elif flow_z <= -0.5:
            dilution_delta = -5
            dilution_note  = f"Caudal levemente bajo (z={flow_z:.1f}): ligera reducción en dilución de contaminantes"
        elif flow_z >= 2.5:
            dilution_delta = -10
            dilution_note  = f"Crecida severa (z={flow_z:.1f}): arrastre de sedimentos y agroquímicos por escorrentía superficial"
        elif flow_z >= 1.5:
            dilution_delta = -4
            dilution_note  = f"Caudal elevado (z={flow_z:.1f}): mayor dilución pero leve aumento de turbidez y escorrentía agrícola"
        else:
            dilution_delta = 0
            dilution_note  = f"Caudal normal (z={flow_z:.1f}): dilución en valores históricos típicos"

    score = max(0, min(100, base + dilution_delta))

    # Classify
    if   score >= 75: level, qlabel = "green",  "Buena"
    elif score >= 55: level, qlabel = "yellow", "Regular"
    elif score >= 35: level, qlabel = "orange", "Deficiente"
    else:             level, qlabel = "red",    "Crítica"

    return {
        "basin_id":       basin_id,
        "score":          score,
        "level":          level,
        "label":          qlabel,
        "base_score":     base,
        "dilution_delta": dilution_delta,
        "dilution_note":  dilution_note,
        "flow_z":         flow_z,
        "flow_status":    flow_label,
        "flow_pct":       flow_pct,
        "pressures":      _WQ_PRESSURE.get(basin_id, []),
        "model_note":     (
            "Índice estimado mediante modelo ICA simplificado: score base (presión antrópica curada) "
            "± modificador de dilución por caudal actual. No sustituye monitoreo fisicoquímico oficial (INA/CONICET). "
            "Fuentes: INA, ACUMAR, CONICET, UNESCO-PHI."
        ),
    }


@app.get("/api/water/quality-index")
def get_quality_index(basin: str = Query(None, description="basin_id (ej: negro_limay). Sin parámetro → todos")):
    """Índice de Calidad del Agua dinámico (ICA simplificado).
    Combina score de presión antrópica base con modificador por anomalía
    de caudal actual (efecto dilución). Devuelve nivel, score 0–100, factores
    y nota metodológica."""
    if basin:
        result = _compute_wqi(basin)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Sin datos de calidad para '{basin}'")
        return result
    # All basins
    return {
        "basins": [r for r in (_compute_wqi(bid) for bid in _WQ_BASE) if r],
        "model_version": "1.0-simplified",
        "note": "Modelo ICA simplificado: presión antrópica base + dilución por caudal actual.",
    }


def _enrich_water_body(wb_id: str, wb: dict) -> dict:
    """Enriquece un cuerpo de agua con tendencia lineal, estadísticas y anomalía actual."""
    import math, copy
    w = copy.deepcopy(wb)
    years = WATER_BODIES["metadata"]["years"]
    data  = w.get("data", [])
    n = len(data)
    if n < 3:
        return w

    # Tendencia lineal (mínimos cuadrados) sobre valores anuales
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(data) / n
    num   = sum((xs[i] - x_mean) * (data[i] - y_mean) for i in range(n))
    denom = sum((xs[i] - x_mean) ** 2 for i in range(n))
    slope_per_year = num / denom if denom else 0.0   # km²/año

    # Cambio porcentual (último vs primero)
    first_val = data[0]
    last_val  = data[-1]
    pct_change = round((last_val - first_val) / first_val * 100, 1) if first_val else 0.0

    # Máximo y mínimo con año
    max_val = max(data); max_yr = years[data.index(max_val)]
    min_val = min(data); min_yr = years[data.index(min_val)]

    # σ (población) y z-score del valor actual
    variance  = sum((v - y_mean) ** 2 for v in data) / n
    std_dev   = math.sqrt(variance)
    z_current = round((last_val - y_mean) / std_dev, 2) if std_dev else 0.0

    w["stats"] = {
        "first_year":       years[0],
        "last_year":        years[-1],
        "first_value":      round(first_val, 1),
        "last_value":       round(last_val, 1),
        "pct_change":       pct_change,
        "slope_per_year":   round(slope_per_year, 2),
        "mean":             round(y_mean, 1),
        "std_dev":          round(std_dev, 1),
        "max_value":        round(max_val, 1),
        "max_year":         max_yr,
        "min_value":        round(min_val, 1),
        "min_year":         min_yr,
        "z_current":        z_current,
        "trend_line_start": round(y_mean + slope_per_year * (0 - x_mean), 1),
        "trend_line_end":   round(y_mean + slope_per_year * (n - 1 - x_mean), 1),
    }
    w["years"] = years
    w["id"]    = wb_id
    return w


@app.get("/api/water/surface-change")
def get_water_surface_change(id: str = Query(None, description="ID del cuerpo de agua (ej: colhue_huapi)")):
    """Superficie histórica de cuerpos de agua derivada de Landsat/Sentinel GSW-JRC (1990–2025).
    Sin parámetros retorna el índice; con ?id= retorna la serie completa enriquecida."""
    bodies = WATER_BODIES.get("water_bodies", {})
    if id:
        if id not in bodies:
            raise HTTPException(status_code=404, detail=f"Cuerpo de agua '{id}' no encontrado")
        return _enrich_water_body(id, bodies[id])
    # Índice liviano
    return {
        "water_bodies": [
            {
                "id":    k,
                "name":  v["name"],
                "name_short": v["name_short"],
                "trend": v["trend"],
                "type":  v["type"],
                "basin": v.get("basin"),
                "coords": v["coords"],
                "unit":  v["unit"],
            }
            for k, v in bodies.items()
        ],
        "metadata": WATER_BODIES.get("metadata", {}),
    }


@app.get("/api/climate/chirps")
def get_chirps(basin: str = Query(None, description="basin_id (ej: negro_limay). Sin param → índice de todas las cuencas")):
    """Precipitación anual por cuenca derivada de CHIRPS v2.0 (CHC/UCSB), 1981–2024.
    - Sin parámetros: índice liviano con media base y anomalía 2024.
    - Con ?basin=: serie completa + estadísticas para la cuenca seleccionada."""
    basins_data = CHIRPS.get("basins", {})
    if basin:
        if basin not in basins_data:
            raise HTTPException(status_code=404, detail=f"Cuenca '{basin}' sin datos CHIRPS")
        bd = basins_data[basin]
        years = CHIRPS["metadata"]["years"]
        # Quintiles para clasificar el año actual
        vals = sorted(bd["data"])
        n = len(vals)
        pct_2024 = sum(1 for v in vals if v < bd["data"][-1]) / n * 100
        return {
            "basin": basin,
            "name": bd["name"],
            "metadata": CHIRPS["metadata"],
            "mean_base": bd["mean_base"],
            "std_base": bd["std_base"],
            "anomaly_2024_mm": bd["anomaly_2024_mm"],
            "anomaly_2024_pct": bd["anomaly_2024_pct"],
            "z_2024": bd["z_2024"],
            "percentile_2024": round(pct_2024),
            "series": [{"year": y, "precip_mm": v} for y, v in zip(years, bd["data"])],
        }
    # Índice liviano
    def _classify(z):
        if z <= -1.5: return "muy_seco"
        if z <= -0.5: return "seco"
        if z <   0.5: return "normal"
        if z <   1.5: return "húmedo"
        return "muy_húmedo"

    return {
        "metadata": CHIRPS["metadata"],
        "basins": [
            {
                "id": bid,
                "name": bd["name"],
                "mean_base": bd["mean_base"],
                "anomaly_2024_mm": bd["anomaly_2024_mm"],
                "anomaly_2024_pct": bd["anomaly_2024_pct"],
                "z_2024": bd["z_2024"],
                "status_2024": _classify(bd["z_2024"]),
            }
            for bid, bd in basins_data.items()
        ],
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
    Susceptibilidad a inundación basada en HAND (Height Above Nearest Drainage).
    HAND es el indicador más validado en la literatura para susceptibilidad fluvial
    (Nobre et al. 2011, JRC Global Flood Awareness System).

    Factores (total 100 pts):
      - HAND — altura sobre el cauce más cercano  (0–50 pts) ← primario
      - Magnitud del río (Strahler + caudal)       (0–20 pts)
      - Pendiente local SRTM ~1 km                 (0–15 pts)
      - Índice de posición topográfica (TPI)        (0–15 pts)

    Todo en una sola llamada a OpenTopoData (6 puntos batch).
    """
    import math

    pt = Point(lng, lat)
    lat_rad     = abs(lat) * math.pi / 180
    km_per_deg  = 111.0 * math.cos(lat_rad)   # lon → km at this latitude

    # ── 1) Find nearest river segment (STRtree) ─────────────────────────────
    SEARCH_DEG = 0.5
    candidates  = _RIVER_TREE.query(pt.buffer(SEARCH_DEG))

    river_dist_km      = None
    nearest_strahler   = 0
    nearest_discharge  = 0.0
    nearest_river_name = None
    nearest_river_pt   = None   # shapely Point on the river line (for HAND)

    if len(candidates):
        best_dist = float("inf")
        best_idx  = None
        for idx in candidates:
            d = pt.distance(_geom_lines[idx])
            if d < best_dist:
                best_dist = d
                best_idx  = idx

        if best_idx is not None:
            river_dist_km = round(best_dist * km_per_deg, 2)
            seg_props = RIVERS_GEOM["features"][best_idx]["properties"]
            nearest_strahler   = seg_props.get("s", 0)
            nearest_discharge  = seg_props.get("q", 0.0) or 0.0
            nearest_river_name = seg_props.get("name") or RIVERS_NAMES.get(_geom_ids[best_idx])
            # Exact nearest point ON the river geometry → needed for HAND elevation
            nearest_river_pt   = _shp_nearest_points(_geom_lines[best_idx], pt)[0]

    # ── 2) Batch elevation query: center + 4 slope offsets + river point ────
    D_LAT, D_LNG = 0.009, 0.011   # ~1 km offsets
    elev_query = [
        (lat,           lng),          # 0 — center
        (lat + D_LAT,   lng),          # 1 — N
        (lat - D_LAT,   lng),          # 2 — S
        (lat,           lng + D_LNG),  # 3 — E
        (lat,           lng - D_LNG),  # 4 — W
    ]
    if nearest_river_pt is not None:
        elev_query.append((nearest_river_pt.y, nearest_river_pt.x))   # 5 — river

    elevations     = _fetch_elevations(elev_query)
    elev_center    = elevations[0]
    elev_surround  = [e for e in elevations[1:5] if e is not None]
    elev_river     = elevations[5] if len(elevations) > 5 else None

    # ── 3) Compute HAND ─────────────────────────────────────────────────────
    # HAND = elevation above the nearest drainage channel (SRTM-based).
    # Guard: if elev_river ≥ elev_center it means the nearest 2D segment is a
    # hillside tributary at the same or higher altitude — HAND is indeterminate.
    # In that case, fall back to 2D distance scoring (hand_m stays None).
    hand_m = None
    hand_indeterminate = False
    if elev_center is not None and elev_river is not None:
        raw = elev_center - elev_river
        if raw > 1.0:
            # Clear case: we are above the river
            hand_m = round(raw, 1)
        elif raw >= -1.0:
            # Ambiguous: same elevation within SRTM noise (~1 m).
            # Only accept HAND=0 for significant rivers (Strahler ≥ 5); otherwise
            # it's likely a hillside stream and we'd be giving a false flood signal.
            if nearest_strahler >= 5:
                hand_m = 0.0
            else:
                hand_indeterminate = True
        else:
            # River point is higher than click point — clearly a hillside tributary.
            hand_indeterminate = True

    # ── 4) Scoring (0–100 pts) ──────────────────────────────────────────────
    score   = 0
    factors = []

    # 4a) HAND — Height Above Nearest Drainage (0–50 pts) ← PRIMARY
    river_label = nearest_river_name or "cauce más cercano"
    if hand_m is not None:
        if hand_m < 1:
            pts_hand = 50
            hand_desc = "En la llanura de inundación activa — riesgo muy elevado"
        elif hand_m < 3:
            pts_hand = 42
            hand_desc = "Zona de inundación frecuente (retorno ≤ 10 años)"
        elif hand_m < 7:
            pts_hand = 33
            hand_desc = "Zona de inundación ocasional (retorno 10–50 años)"
        elif hand_m < 15:
            pts_hand = 20
            hand_desc = "Riesgo bajo-moderado, inundaciones excepcionales"
        elif hand_m < 30:
            pts_hand = 8
            hand_desc = "Por encima de la llanura de inundación, bajo riesgo"
        else:
            pts_hand = 0
            hand_desc = "Zona alta, sin riesgo fluvial significativo"
        score += pts_hand
        factors.append({
            "id":     "hand",
            "label":  "HAND — Altura sobre el cauce (SRTM)",
            "value":  f"{hand_m:.1f} m",
            "detail": f"{hand_desc} · {river_label} (orden {nearest_strahler})",
            "pts":    pts_hand,
            "max":    50,
        })
    elif river_dist_km is not None:
        # HAND indeterminate (hillside tributary at same altitude) or no SRTM data.
        # Fall back to 2D distance — still informative but lower confidence.
        if river_dist_km < 0.3:
            pts_hand = 30
        elif river_dist_km < 1.0:
            pts_hand = 18
        elif river_dist_km < 3.0:
            pts_hand = 8
        else:
            pts_hand = 2
        score += pts_hand
        fallback_reason = ("SRTM: tributario de ladera descartado" if hand_indeterminate
                           else "SRTM no disponible")
        factors.append({
            "id":     "hand",
            "label":  "Proximidad al cauce (HAND no resuelto)",
            "value":  f"{river_dist_km:.1f} km · {river_label}",
            "detail": f"Orden {nearest_strahler} · {fallback_reason}",
            "pts":    pts_hand,
            "max":    50,
        })
    else:
        factors.append({
            "id": "hand", "label": "HAND — Altura sobre el cauce",
            "value": "N/D", "detail": "Sin ríos en el radio de análisis",
            "pts": 0, "max": 50,
        })

    # 4b) River magnitude — Strahler + discharge (0–20 pts)
    if nearest_strahler and river_dist_km is not None and river_dist_km < 15:
        if nearest_strahler >= 9:
            pts_mag = 20
        elif nearest_strahler >= 7:
            pts_mag = 16
        elif nearest_strahler >= 5:
            pts_mag = 10
        elif nearest_strahler >= 3:
            pts_mag = 5
        else:
            pts_mag = 1
        score += pts_mag
        q_str = f"Q ≈ {nearest_discharge:.0f} m³/s" if nearest_discharge > 0 else "caudal no disponible"
        factors.append({
            "id":    "river_mag",
            "label": "Magnitud del río",
            "value": f"Orden {nearest_strahler}",
            "detail": q_str + f" · {nearest_river_name or 'río sin nombre'}",
            "pts":   pts_mag,
            "max":   20,
        })
    else:
        factors.append({
            "id": "river_mag", "label": "Magnitud del río",
            "value": "—", "detail": "Sin río significativo cercano",
            "pts": 0, "max": 20,
        })

    # 4c) Local slope (0–15 pts) — flat terrain → water accumulates
    if elev_center is not None and len(elev_surround) >= 2:
        max_diff  = max(abs(e - elev_center) for e in elev_surround)
        slope_pct = (max_diff / 1000) * 100
        if slope_pct < 0.5:
            pts_slope = 15; slope_desc = "Terreno muy llano — agua se acumula"
        elif slope_pct < 1.5:
            pts_slope = 11; slope_desc = "Llano — drenaje lento"
        elif slope_pct < 3.0:
            pts_slope = 6;  slope_desc = "Pendiente suave — drenaje moderado"
        elif slope_pct < 8.0:
            pts_slope = 2;  slope_desc = "Pendiente moderada — buen drenaje"
        else:
            pts_slope = 0;  slope_desc = "Terreno pronunciado — escorrentía rápida"
        score += pts_slope
        factors.append({
            "id":    "slope",
            "label": "Pendiente local (SRTM, ~1 km)",
            "value": f"{slope_pct:.1f}%",
            "detail": slope_desc,
            "pts":   pts_slope,
            "max":   15,
        })
    else:
        factors.append({
            "id": "slope", "label": "Pendiente local",
            "value": "N/D", "detail": "Altimetría no disponible",
            "pts": 0, "max": 15,
        })

    # 4d) Topographic Position Index — TPI (0–15 pts)
    if elev_center is not None and len(elev_surround) >= 2:
        mean_surround = sum(elev_surround) / len(elev_surround)
        tpi = mean_surround - elev_center   # positive = depression (flood-prone)
        if tpi > 15:
            pts_tpi = 15; tpi_desc = "Depresión marcada — concentra escorrentía"
        elif tpi > 5:
            pts_tpi = 11; tpi_desc = "Posición ligeramente deprimida"
        elif tpi > 0:
            pts_tpi = 6;  tpi_desc = "Terreno relativamente plano"
        else:
            pts_tpi = 0;  tpi_desc = "Posición elevada respecto al entorno"
        score += pts_tpi
        sign = "+" if tpi >= 0 else ""
        factors.append({
            "id":    "tpi",
            "label": "Índice de posición topográfica (TPI)",
            "value": f"{elev_center:.0f} m s.n.m.",
            "detail": f"TPI = {sign}{tpi:.1f} m · {tpi_desc}",
            "pts":   pts_tpi,
            "max":   15,
        })
    else:
        factors.append({
            "id": "tpi", "label": "Posición topográfica (TPI)",
            "value": "N/D", "detail": "",
            "pts": 0, "max": 15,
        })

    # ── 5) Risk level ────────────────────────────────────────────────────────
    score = min(score, 100)
    if score >= 76:
        risk_level = "Muy Alto"
        risk_color = "red"
        risk_desc  = "Alta probabilidad de anegamiento ante crecidas ordinarias o lluvias intensas."
    elif score >= 51:
        risk_level = "Alto"
        risk_color = "orange"
        risk_desc  = "En o cerca de la llanura de inundación. Riesgo significativo en eventos moderados."
    elif score >= 26:
        risk_level = "Moderado"
        risk_color = "yellow"
        risk_desc  = "Riesgo bajo en condiciones normales, presente ante eventos extremos."
    else:
        risk_level = "Bajo"
        risk_color = "green"
        risk_desc  = "Por encima de la llanura de inundación. Riesgo fluvial bajo."

    return {
        "query":       {"lat": lat, "lng": lng},
        "risk_level":  risk_level,
        "risk_color":  risk_color,
        "risk_desc":   risk_desc,
        "score":       score,
        "score_max":   100,
        "factors":     factors,
        "hand_m":      round(hand_m, 1) if hand_m is not None else None,
        "elevation_m": round(elev_center, 1) if elev_center is not None else None,
        "river_elev_m": round(elev_river, 1) if elev_river is not None else None,
        "method":      "HAND (Height Above Nearest Drainage) + HydroRIVERS STRtree + SRTM30m",
        "note":        "Análisis indicativo. HAND ≈ 30m resolución SRTM. No reemplaza estudios hidráulicos profesionales.",
        "sources":     ["HydroRIVERS (HydroSHEDS)", "SRTM30m vía OpenTopoData", "Nobre et al. 2011 — HAND method"],
    }


def _fetch_soil_clay(lat: float, lng: float) -> float | None:
    """Fetch topsoil clay content (%) from SoilGrids v2.
    Returns None if API fails or data not available for the coordinate."""
    params = urllib.parse.urlencode({
        "lon":      lng,
        "lat":      lat,
        "property": "clay",
        "depth":    "0-5cm",
        "value":    "mean",
    })
    url = f"https://rest.isric.org/soilgrids/v2.0/properties/query?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "AppAgua/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8, context=_SSL_CTX) as r:
            data = json.loads(r.read())
        layers = data.get("properties", {}).get("layers", [])
        for layer in layers:
            if layer.get("name") == "clay":
                for depth in layer.get("depths", []):
                    mean_raw = depth.get("values", {}).get("mean")
                    if mean_raw is not None:
                        # SoilGrids clay in g/kg × 10 → divide by 10 for %
                        d_factor = layer.get("unit_measure", {}).get("d_factor", 10)
                        return round(mean_raw / d_factor, 1)
    except Exception as e:
        print(f"[soilgrids] error: {e}")
    return None


def _nearest_city_km(lat: float, lng: float) -> tuple[float, int, str]:
    """Return (distance_km, population, name) of nearest city in _CITY_COORDS."""
    import math
    best_dist = float("inf")
    best_pop  = 0
    best_name = ""
    lat_rad = abs(lat) * math.pi / 180
    km_per_deg_lng = 111.0 * math.cos(lat_rad)
    for c_lat, c_lng, pop, name in _CITY_COORDS:
        dlat = (c_lat - lat) * 111.0
        dlng = (c_lng - lng) * km_per_deg_lng
        d = (dlat**2 + dlng**2) ** 0.5
        if d < best_dist:
            best_dist = d
            best_pop  = pop
            best_name = name
    return round(best_dist, 1), best_pop, best_name


@app.get("/api/tools/runoff")
def runoff_index(
    lat: float = Query(..., description="Latitud decimal"),
    lng: float = Query(..., description="Longitud decimal"),
):
    """
    Índice de escorrentía pluvial (SCS-CN) para un punto dado.
    Combina:
      - Contenido de arcilla del suelo (SoilGrids v2, ISRIC)
      - Uso del suelo estimado por proximidad a ciudades
      - Pendiente local (SRTM30m vía OpenTopoData)
    Calcula la escorrentía potencial para 3 escenarios de lluvia.
    """
    import math

    # ── 1) Elevation / slope (same 5-point SRTM call as flood tool) ──────────
    D_LAT, D_LNG = 0.009, 0.011
    elev_pts = [
        (lat,         lng),
        (lat + D_LAT, lng),
        (lat - D_LAT, lng),
        (lat,         lng + D_LNG),
        (lat,         lng - D_LNG),
    ]
    elevations    = _fetch_elevations(elev_pts)
    elev_center   = elevations[0]
    elev_surround = [e for e in elevations[1:] if e is not None]

    slope_pct = None
    if elev_center is not None and len(elev_surround) >= 2:
        max_diff  = max(abs(e - elev_center) for e in elev_surround)
        slope_pct = round((max_diff / 1000) * 100, 2)

    # ── 2) Soil clay content → Hydrologic Soil Group ────────────────────────
    clay_pct  = _fetch_soil_clay(lat, lng)
    hsg_label = None
    hsg_desc  = None
    hsg_code  = None   # A, B, C, D

    if clay_pct is not None:
        if clay_pct < 18:
            hsg_code, hsg_label, hsg_desc = "A", "Grupo A (arenoso)", "Alta infiltración. Arena o grava."
        elif clay_pct < 35:
            hsg_code, hsg_label, hsg_desc = "B", "Grupo B (franco)", "Infiltración moderada. Suelos francos."
        elif clay_pct < 45:
            hsg_code, hsg_label, hsg_desc = "C", "Grupo C (franco-arcilloso)", "Infiltración lenta. Tendencia a impermeabilizarse."
        else:
            hsg_code, hsg_label, hsg_desc = "D", "Grupo D (arcilloso)", "Muy baja infiltración. Alta escorrentía."
    else:
        # Fallback: estimate from slope (steep Andes→A, flat Pampa→C/D)
        if slope_pct is not None:
            if slope_pct > 10:
                hsg_code, hsg_label, hsg_desc = "A", "Grupo A (estimado)", "Terreno escarpado — infiltración rápida estimada."
            elif slope_pct > 3:
                hsg_code, hsg_label, hsg_desc = "B", "Grupo B (estimado)", "Pendiente moderada — infiltración media estimada."
            else:
                hsg_code, hsg_label, hsg_desc = "C", "Grupo C (estimado)", "Terreno llano — infiltración lenta estimada."
        else:
            hsg_code, hsg_label, hsg_desc = "B", "Grupo B (estimado)", "Dato de suelo no disponible — valor intermedio asumido."

    # ── 3) Land cover from city proximity ───────────────────────────────────
    city_dist_km, city_pop, city_name = _nearest_city_km(lat, lng)
    if city_dist_km < 3 and city_pop > 50_000:
        lc_code  = "urban"
        lc_label = "Urbano / Impermeabilizado"
        lc_desc  = f"Zona urbana (a {city_dist_km:.1f} km de {city_name})"
    elif city_dist_km < 10 and city_pop > 20_000:
        lc_code  = "suburban"
        lc_label = "Periurbano / Residencial"
        lc_desc  = f"Área periurbana (a {city_dist_km:.1f} km de {city_name})"
    elif city_dist_km < 5 and city_pop > 5_000:
        lc_code  = "suburban"
        lc_label = "Periurbano / Residencial"
        lc_desc  = f"Entorno de localidad (a {city_dist_km:.1f} km de {city_name})"
    else:
        lc_code  = "rural"
        lc_label = "Rural / Natural"
        lc_desc  = "Zona sin urbanización significativa en el radio de análisis"

    # ── 4) SCS Curve Number ──────────────────────────────────────────────────
    # CN table: {"land_use": [A, B, C, D]}
    CN_TABLE = {
        "urban":    [77, 85, 90, 92],
        "suburban": [61, 75, 83, 87],
        "rural":    [39, 61, 74, 80],
    }
    hsg_idx = {"A": 0, "B": 1, "C": 2, "D": 3}.get(hsg_code, 1)
    cn = CN_TABLE[lc_code][hsg_idx]

    # Adjust CN for slope (AMC II → slope correction, simplified)
    if slope_pct is not None and slope_pct > 5:
        cn = min(cn + 3, 98)

    # ── 5) SCS-CN runoff for 3 scenarios ────────────────────────────────────
    def scs_runoff(P_mm: float, CN: int) -> dict:
        S = 25.4 * (1000 / CN - 10)       # potential max retention (mm)
        Ia = 0.2 * S                        # initial abstraction
        if P_mm <= Ia:
            Q = 0.0
        else:
            Q = (P_mm - Ia) ** 2 / (P_mm - Ia + S)
        infil = P_mm - Q
        return {
            "P_mm":          round(P_mm, 0),
            "runoff_mm":     round(Q, 1),
            "infiltration_mm": round(infil, 1),
            "runoff_pct":    round(Q / P_mm * 100, 1) if P_mm > 0 else 0,
        }

    scenarios = [
        {"label": "Lluvia moderada", "P_mm": 30,  "return_yr": "~2 años",  "emoji": "🌦"},
        {"label": "Lluvia intensa",  "P_mm": 80,  "return_yr": "~10 años", "emoji": "🌧"},
        {"label": "Lluvia extrema",  "P_mm": 160, "return_yr": "~100 años","emoji": "⛈"},
    ]
    for sc in scenarios:
        sc.update(scs_runoff(sc["P_mm"], cn))

    # Overall runoff propensity label (based on 80mm scenario)
    q80 = scenarios[1]["runoff_pct"]
    if q80 > 65:
        tendency = "Alta escorrentía"
        tendency_color = "red"
    elif q80 > 40:
        tendency = "Escorrentía moderada"
        tendency_color = "yellow"
    elif q80 > 20:
        tendency = "Infiltración predominante"
        tendency_color = "green"
    else:
        tendency = "Alta infiltración"
        tendency_color = "green"

    return {
        "query":    {"lat": lat, "lng": lng},
        "curve_number": cn,
        "tendency":       tendency,
        "tendency_color": tendency_color,
        "hsg": {
            "code":  hsg_code,
            "label": hsg_label,
            "desc":  hsg_desc,
            "clay_pct": clay_pct,
        },
        "land_cover": {
            "code":  lc_code,
            "label": lc_label,
            "desc":  lc_desc,
        },
        "slope_pct":    slope_pct,
        "elevation_m":  round(elev_center, 1) if elev_center is not None else None,
        "scenarios":    scenarios,
        "note":    "Método SCS-CN (USDA). Estimación indicativa para eventos de lluvia puntual. "
                   "No incluye efectos de urbanización, canal, obras hidráulicas ni condición de humedad antecedente.",
        "sources": ["SoilGrids v2 (ISRIC)", "SRTM30m vía OpenTopoData", "SCS-CN USDA TR-55"],
    }


@app.get("/api/tools/aquifer")
def aquifer_potential(
    lat: float = Query(..., description="Latitud decimal"),
    lng: float = Query(..., description="Longitud decimal"),
):
    """
    Potencial acuífero para un punto dado.
    Combina:
      - Intersección espacial con ar_aquifers.geojson (8 sistemas curados)
      - Posición topográfica y pendiente (SRTM30m) → recarga potencial
      - Proximidad al río más cercano → conexión fluvio-acuífera
    """
    import math

    pt = Point(lng, lat)

    # ── 1) Aquifer polygon lookup ────────────────────────────────────────────
    matched_aquifer = None
    for aq_shape, aq_props in _AQUIFER_SHAPES:
        if aq_shape.contains(pt):
            matched_aquifer = aq_props
            break

    # ── 2) Elevation / slope ─────────────────────────────────────────────────
    D_LAT, D_LNG = 0.009, 0.011
    elev_pts = [
        (lat,         lng),
        (lat + D_LAT, lng),
        (lat - D_LAT, lng),
        (lat,         lng + D_LNG),
        (lat,         lng - D_LNG),
    ]
    elevations    = _fetch_elevations(elev_pts)
    elev_center   = elevations[0]
    elev_surround = [e for e in elevations[1:] if e is not None]

    slope_pct = None
    topo_diff = None
    if elev_center is not None and len(elev_surround) >= 2:
        max_diff  = max(abs(e - elev_center) for e in elev_surround)
        slope_pct = round((max_diff / 1000) * 100, 2)
        mean_surround = sum(elev_surround) / len(elev_surround)
        topo_diff = round(mean_surround - elev_center, 1)

    # ── 3) Nearest river distance ────────────────────────────────────────────
    SEARCH_DEG = 0.3
    search_buf = pt.buffer(SEARCH_DEG)
    candidates = _RIVER_TREE.query(search_buf)
    river_dist_km = None
    nearest_strahler = 0
    if len(candidates):
        best_dist = float("inf")
        best_idx  = None
        for idx in candidates:
            d = pt.distance(_geom_lines[idx])
            if d < best_dist:
                best_dist = d
                best_idx  = idx
        if best_idx is not None:
            lat_rad = abs(lat) * math.pi / 180
            km_per_deg = 111.0 * math.cos(lat_rad)
            river_dist_km   = round(best_dist * km_per_deg, 2)
            nearest_strahler = RIVERS_GEOM["features"][best_idx]["properties"].get("s", 0)

    # ── 4) Recharge potential score (if outside mapped aquifers) ─────────────
    recharge_score = 0
    recharge_factors = []

    # Slope: moderate is best for recharge (flat = runoff pools, steep = runoff runs off)
    if slope_pct is not None:
        if 0.5 <= slope_pct <= 5:
            pts_slope = 30
            slope_desc = "Pendiente óptima para recarga"
        elif slope_pct < 0.5:
            pts_slope = 15
            slope_desc = "Muy llano — puede generar encharcamiento"
        elif slope_pct <= 15:
            pts_slope = 20
            slope_desc = "Pendiente moderada — recarga aceptable"
        else:
            pts_slope = 5
            slope_desc = "Pendiente pronunciada — alta escorrentía, baja recarga"
        recharge_score += pts_slope
        recharge_factors.append({"label": "Pendiente local", "value": f"{slope_pct:.1f}%",
                                  "desc": slope_desc, "pts": pts_slope, "max": 30})

    # Topo position: slight depression → water accumulates → recharge
    if topo_diff is not None:
        if topo_diff > 5:
            pts_topo = 25
            topo_desc = "Zona deprimida — concentra escorrentía y recarga"
        elif topo_diff >= 0:
            pts_topo = 15
            topo_desc = "Posición ligeramente baja — favorece la recarga"
        else:
            pts_topo = 5
            topo_desc = "Posición elevada — agua drena hacia aguas abajo"
        recharge_score += pts_topo
        recharge_factors.append({"label": "Posición topográfica", "value": f"{elev_center:.0f} m s.n.m." if elev_center else "N/D",
                                  "desc": topo_desc, "pts": pts_topo, "max": 25})

    # River proximity: close to river = higher water table, better connectivity
    if river_dist_km is not None:
        if river_dist_km < 1 and nearest_strahler >= 5:
            pts_river = 25
            river_desc = f"Conexión fluvio-acuífera directa (río orden {nearest_strahler})"
        elif river_dist_km < 3:
            pts_river = 18
            river_desc = f"Proximidad a río (orden {nearest_strahler}) — posible zona de recarga"
        elif river_dist_km < 10:
            pts_river = 10
            river_desc = "Zona de influencia fluvial indirecta"
        else:
            pts_river = 3
            river_desc = "Lejos de cursos de agua principales"
        recharge_score += pts_river
        dist_str = f"{river_dist_km:.1f} km"
        recharge_factors.append({"label": "Proximidad a río", "value": dist_str,
                                  "desc": river_desc, "pts": pts_river, "max": 25})

    # Scale recharge to 0-100 (max possible is 30+25+25 = 80)
    recharge_pct = min(round(recharge_score / 80 * 100), 100)
    if recharge_pct >= 65:
        recharge_label = "Alto"
        recharge_color = "green"
    elif recharge_pct >= 40:
        recharge_label = "Moderado"
        recharge_color = "yellow"
    else:
        recharge_label = "Bajo"
        recharge_color = "red"

    return {
        "query":      {"lat": lat, "lng": lng},
        "in_aquifer": matched_aquifer is not None,
        "aquifer":    matched_aquifer,       # full aquifer props if matched
        "recharge": {
            "score":   recharge_pct,
            "label":   recharge_label,
            "color":   recharge_color,
            "factors": recharge_factors,
        },
        "elevation_m":       round(elev_center, 1) if elev_center is not None else None,
        "slope_pct":         slope_pct,
        "river_dist_km":     river_dist_km,
        "river_strahler":    nearest_strahler,
        "note":   "Basado en 8 sistemas acuíferos curados (SEGEMAR/INA/SAG-UNESCO) y análisis SRTM. "
                  "La ausencia de un acuífero mapeado no implica ausencia de agua subterránea.",
        "sources": ["SEGEMAR / INA / SAG-UNESCO (acuíferos)", "SRTM30m vía OpenTopoData"],
    }
