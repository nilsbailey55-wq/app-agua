"""
generate_land_report.py
Prototipo del producto "Reporte de Riesgo Hídrico para Campo".

Input:  --lat, --lng, --area-ha (opcional), --owner (opcional)
Output: HTML self-contained en /tmp/land_report_<slug>.html

Datos usados (todos reales del backend):
 - basins.json + basin_geometries → identificación cuenca
 - precip_heatmap.json → climatología NASA POWER (grilla 2°)
 - chirps_basin_precip.json → serie histórica 1981-2024 cuenca
 - ar_flow_series.json → caudal cuenca
 - water_body_area.json → cuerpos de agua cercanos
 - CONAE WMS → estado actual (linkeado, no embebido)
"""

import json
import math
import argparse
import sys
import urllib.parse
from datetime import date
from pathlib import Path
from shapely.geometry import shape, Point

DATA = Path(__file__).parent.parent / "data"

# Map renderer (Pillow + CARTO tiles)
sys.path.insert(0, str(Path(__file__).parent))
try:
    from _map_renderer import render_to_base64
    MAP_AVAILABLE = True
except Exception as e:
    MAP_AVAILABLE = False
    print(f"[warn] Map renderer no disponible: {e}", file=sys.stderr)

# ── helpers ────────────────────────────────────────────────────────────────
def load(name):
    with open(DATA / name) as f:
        return json.load(f)

def linreg(years, values):
    """Regresión lineal simple. Devuelve (slope, intercept, r)."""
    n = len(years)
    mx, my = sum(years)/n, sum(values)/n
    sxy = sum((x-mx)*(y-my) for x, y in zip(years, values))
    sxx = sum((x-mx)**2 for x in years)
    syy = sum((y-my)**2 for y in values)
    slope = sxy / sxx if sxx else 0
    intercept = my - slope * mx
    r = sxy / math.sqrt(sxx*syy) if (sxx*syy) > 0 else 0
    return slope, intercept, r

def bilinear_from_grid(lat, lng, points):
    """Interpolación bilineal en la grilla 2° NASA POWER."""
    # Buscar los 4 puntos vecinos
    lat0 = math.floor(lat/2)*2 + 1   # grilla en lats impares -55,-53,...
    lat1 = lat0 + 2
    lng0 = math.floor(lng/2)*2       # grilla en lons pares -74,-72,...
    lng1 = lng0 + 2
    by_coord = {(p['lat'], p['lon']): p for p in points}
    corners = {
        (lat0, lng0): by_coord.get((lat0, lng0)),
        (lat0, lng1): by_coord.get((lat0, lng1)),
        (lat1, lng0): by_coord.get((lat1, lng0)),
        (lat1, lng1): by_coord.get((lat1, lng1)),
    }
    return corners

def interp_value(lat, lng, corners, field_getter):
    """Bilinear de un campo específico (función getter sobre el punto)."""
    vals, weights = [], []
    for (la, lo), p in corners.items():
        if not p: continue
        v = field_getter(p)
        if v is None or (isinstance(v, float) and math.isnan(v)): continue
        # Peso inverso a distancia (bilinear simplificado)
        dx = 2 - abs(lng - lo)
        dy = 2 - abs(lat - la)
        w = max(0, dx) * max(0, dy)
        if w > 0:
            vals.append(v * w); weights.append(w)
    if not weights: return None
    return sum(vals) / sum(weights)

# ── lógica de scoring ──────────────────────────────────────────────────────
def drought_risk_score(slope_per_year, cv, recent_anom_pct, mean_yearly):
    """0-100. Combina tendencia, variabilidad y estado reciente."""
    # Tendencia: aridización suma puntos
    trend_score = max(0, min(40, abs(min(0, slope_per_year)) * 8))
    # Variabilidad: más CV = más riesgo
    var_score = min(30, cv * 100)
    # Estado reciente: si anom 2024 es muy negativa, suma
    recent_score = max(0, min(30, -recent_anom_pct * 0.6))
    return round(trend_score + var_score + recent_score)

def excess_risk_score(historical_extremes, body_growth_count):
    """0-100. Combina máximos históricos y cuerpos de agua cercanos en crecimiento."""
    extreme_score = min(60, historical_extremes * 15)
    body_score = min(40, body_growth_count * 20)
    return round(extreme_score + body_score)

def color_for_score(s):
    if s >= 70: return '#c62828'  # rojo
    if s >= 50: return '#ef6c00'  # naranja
    if s >= 30: return '#f9a825'  # amarillo
    return '#2e7d32'              # verde

def color_class(s):
    if s >= 70: return 'Alto'
    if s >= 50: return 'Medio-Alto'
    if s >= 30: return 'Medio'
    return 'Bajo'

# ── generador principal ────────────────────────────────────────────────────
def generate(lat, lng, area_ha=None, owner=None, parcel_id=None):
    pt = Point(lng, lat)

    # 1) Cuenca
    basins = load('basins.json')
    geoms  = load('basin_geometries.json') if (DATA / 'basin_geometries.json').exists() else {}
    basin = None
    for b in basins:
        geom = geoms.get(b['id']) or b.get('geometry')
        if not geom: continue
        try:
            if shape(geom).contains(pt):
                basin = b
                break
        except Exception: pass
    if not basin:
        raise RuntimeError(f"Coordenada ({lat}, {lng}) fuera de cuencas conocidas")

    # 2) Climatología (NASA POWER bilinear)
    heatmap = load('precip_heatmap.json')
    corners = bilinear_from_grid(lat, lng, heatmap['points'])
    normal_91_20 = interp_value(lat, lng, corners, lambda p: p['normal_91_20'])
    annual_2024  = interp_value(lat, lng, corners, lambda p: p['annual'].get('2024'))
    annual_2023  = interp_value(lat, lng, corners, lambda p: p['annual'].get('2023'))
    monthly_2024 = [interp_value(lat, lng, corners, lambda p, i=i: p['monthly_2024'][i])
                    for i in range(12)]

    # 3) CHIRPS serie histórica
    chirps = load('chirps_basin_precip.json')
    chirps_basin = chirps['basins'].get(basin['id'])
    if chirps_basin:
        years = chirps['metadata']['years']
        series = chirps_basin['data']
        slope, intercept, r = linreg(years, series)
        cv = chirps_basin['std_base'] / chirps_basin['mean_base']
        # Década más seca y más húmeda
        decades = {}
        for y, v in zip(years, series):
            d = (y // 10) * 10
            decades.setdefault(d, []).append(v)
        # Solo décadas con ≥8 años (evita 2020s que está incompleta a 2024)
        decade_means = {d: sum(vs)/len(vs) for d, vs in decades.items() if len(vs) >= 8}
        wettest_decade = max(decade_means.items(), key=lambda kv: kv[1])
        driest_decade  = min(decade_means.items(), key=lambda kv: kv[1])
        # Años extremos
        extreme_dry = sum(1 for v in series if v < chirps_basin['mean_base'] - 1.5 * chirps_basin['std_base'])
        extreme_wet = sum(1 for v in series if v > chirps_basin['mean_base'] + 1.5 * chirps_basin['std_base'])
    else:
        slope, cv, wettest_decade, driest_decade = 0, 0, (0, 0), (0, 0)
        extreme_dry, extreme_wet = 0, 0

    # 4) Caudal
    flow = load('ar_flow_series.json')
    flow_series = flow.get('series', {}).get(basin['id'])
    flow_metric = flow_series['metrics'][0] if flow_series and flow_series.get('metrics') else None

    # 5) Cuerpos de agua cercanos
    water_bodies = load('water_body_area.json')
    nearby_bodies = []
    for wb_id, wb in water_bodies['water_bodies'].items():
        wlat, wlng = wb['coords']
        dist_km = math.hypot((wlat-lat)*111, (wlng-lng)*111*math.cos(math.radians(lat)))
        if dist_km <= 250:
            # Crecimiento últimos 10 años
            data = wb['data']
            recent_avg = sum(data[-10:]) / 10
            prev_avg   = sum(data[-20:-10]) / 10
            growth_pct = ((recent_avg - prev_avg) / prev_avg * 100) if prev_avg else 0
            nearby_bodies.append({
                'name': wb['name'], 'dist_km': round(dist_km), 'trend': wb['trend'],
                'growth_pct': round(growth_pct), 'type': wb['type'],
                'unit': wb['unit'], 'latest': data[-1], 'mean': wb['historical_mean'],
            })
    nearby_bodies.sort(key=lambda x: x['dist_km'])
    growing_count = sum(1 for b in nearby_bodies if b['growth_pct'] > 30)

    # 6) Climatología mensual (NASA POWER 1991-2020)
    monthly_normal = [interp_value(lat, lng, corners, lambda p, i=i: p.get('monthly_normal_91_20', [None]*12)[i])
                      for i in range(12)]
    monthly_normal = [round(m, 1) if m else None for m in monthly_normal]
    # Estacionalidad: % en trimestre húmedo (DEF) vs seco (JJA)
    if all(m is not None for m in monthly_normal):
        total = sum(monthly_normal)
        wet_q  = (monthly_normal[11] + monthly_normal[0] + monthly_normal[1]) / total * 100
        dry_q  = (monthly_normal[5]  + monthly_normal[6] + monthly_normal[7]) / total * 100
        wet_month_idx = max(range(12), key=lambda i: monthly_normal[i])
        dry_month_idx = min(range(12), key=lambda i: monthly_normal[i])
    else:
        wet_q, dry_q, wet_month_idx, dry_month_idx = None, None, None, None

    # 7) Acuíferos subyacentes (point-in-polygon)
    aquifers_gj = load('ar_aquifers.geojson')
    subjacent_aquifers = []
    for f in aquifers_gj['features']:
        try:
            if shape(f['geometry']).contains(pt):
                subjacent_aquifers.append(f['properties'])
        except Exception:
            pass

    # 8) Comparables vs cuenca: posición en el ranking de lluvia
    cuenca_points = []
    for p in heatmap['points']:
        # Filtrar puntos dentro de la cuenca (point-in-polygon contra basin geom)
        try:
            basin_geom = shape(geoms.get(basin['id']) or basin.get('geometry'))
            if basin_geom.contains(Point(p['lon'], p['lat'])):
                cuenca_points.append(p['normal_91_20'])
        except Exception:
            pass
    if cuenca_points and normal_91_20:
        cuenca_points.sort()
        percentile_in_basin = sum(1 for v in cuenca_points if v < normal_91_20) / len(cuenca_points) * 100
        cuenca_mean = sum(cuenca_points) / len(cuenca_points)
        diff_pct = (normal_91_20 - cuenca_mean) / cuenca_mean * 100
    else:
        percentile_in_basin, cuenca_mean, diff_pct = None, None, None

    # 9) SPEI-like: frecuencia de sequías por umbrales (sobre CHIRPS cuenca)
    if chirps_basin:
        mean, std = chirps_basin['mean_base'], chirps_basin['std_base']
        # Eventos por categoría (z-score)
        droughts_mild     = sum(1 for v in series if -1.0 >= (v - mean)/std > -1.5)
        droughts_severe   = sum(1 for v in series if -1.5 >= (v - mean)/std > -2.0)
        droughts_extreme  = sum(1 for v in series if (v - mean)/std <= -2.0)
        # Frecuencia (1 cada X años)
        n_years = len(series)
        freq_drought_any = (droughts_mild + droughts_severe + droughts_extreme)
        years_per_dry    = n_years / freq_drought_any if freq_drought_any > 0 else None
        # Última sequía severa o peor
        last_severe_idx = next((i for i in range(len(series)-1, -1, -1)
                                if (series[i] - mean)/std <= -1.5), None)
        last_severe_year = chirps['metadata']['years'][last_severe_idx] if last_severe_idx is not None else None
    else:
        droughts_mild = droughts_severe = droughts_extreme = 0
        years_per_dry = None
        last_severe_year = None

    # 10) Scores
    anom_2024 = ((annual_2024 - normal_91_20) / normal_91_20 * 100) if (annual_2024 and normal_91_20) else 0
    drought_score = drought_risk_score(slope, cv, anom_2024, normal_91_20 or 800)
    excess_score  = excess_risk_score(extreme_wet, growing_count)
    composite     = round(drought_score * 0.55 + excess_score * 0.45)

    # 10) Topografía (Open-Elevation: parcela + 8 vecinos a ~2 km)
    import urllib.request, urllib.error, ssl as _ssl
    _topo_ctx = _ssl._create_unverified_context()
    topo = None
    try:
        d_lat = 0.018   # ~2 km
        d_lng = 0.018 / max(math.cos(math.radians(lat)), 0.3)
        pts = [(lat, lng)]
        for dla, dln in [(0, 1), (0, -1), (1, 0), (-1, 0),
                         (1, 1), (1, -1), (-1, 1), (-1, -1)]:
            pts.append((lat + dla * d_lat, lng + dln * d_lng))
        locs = '|'.join(f"{la},{ln}" for la, ln in pts)
        url = f"https://api.open-elevation.com/api/v1/lookup?locations={locs}"
        req = urllib.request.Request(url, headers={'User-Agent': 'AppAgua/1.0'})
        with urllib.request.urlopen(req, timeout=15, context=_topo_ctx) as _resp:
            elev_data = json.loads(_resp.read())
        elevs = [e['elevation'] for e in elev_data.get('results', [])]
        if len(elevs) >= 5:
            field_elev = elevs[0]
            neighbors  = elevs[1:]
            mean_neigh = sum(neighbors) / len(neighbors)
            range_total = max(elevs) - min(elevs)
            relative = field_elev - mean_neigh
            slope_m_per_km = range_total / 2  # range sobre ~2km de radio → ~m/km estimado
            if relative > 1.5:
                topo_diag = 'Posición elevada — buen drenaje'
                topo_anega = 'Bajo'
            elif relative < -1.5:
                topo_diag = 'Depresión local — agua converge'
                topo_anega = 'Elevado'
            else:
                topo_diag = 'Topografía similar al entorno'
                topo_anega = 'Medio'
            topo = {
                'field_elev': round(field_elev),
                'mean_neigh': round(mean_neigh),
                'relative':   round(relative, 1),
                'range':      round(range_total),
                'slope':      round(slope_m_per_km, 1),
                'diagnostic': topo_diag,
                'flood_risk': topo_anega,
            }
    except Exception as e:
        print(f"[warn] Topografía no disponible: {e}", file=sys.stderr)

    # 11) Proyecciones CMIP6 (precomputado, country-level Argentina)
    cmip = None
    cmip_path = DATA / 'ar_climate_projections.json'
    if cmip_path.exists():
        with open(cmip_path) as f:
            cmip = json.load(f)

    # 11b) NDVI Sentinel-2 vía Microsoft Planetary Computer (sin auth)
    import base64 as _b64
    ndvi_b64, ndvi_meta = None, None
    try:
        # 1. STAC search: imagen Sentinel-2 reciente con poca nubosidad
        from datetime import timedelta as _td
        today = date.today()
        # Buscar últimos 90 días
        date_range = f"{(today - _td(days=90)).isoformat()}/{today.isoformat()}"
        bbox = [lng-0.025, lat-0.025, lng+0.025, lat+0.025]   # ~5 km × 5 km
        body = {
            "collections": ["sentinel-2-l2a"],
            "bbox": bbox,
            "datetime": date_range,
            "query": {"eo:cloud_cover": {"lt": 20}},
            "sortby": [{"field": "properties.datetime", "direction": "desc"}],
            "limit": 1,
        }
        stac_req = urllib.request.Request(
            "https://planetarycomputer.microsoft.com/api/stac/v1/search",
            method="POST",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "AppAgua/1.0"}
        )
        with urllib.request.urlopen(stac_req, timeout=20, context=_topo_ctx) as _resp:
            sd = json.loads(_resp.read())
        feats = sd.get('features', [])
        if feats:
            item = feats[0]
            item_id = item['id']
            cloud  = item['properties'].get('eo:cloud_cover', 0)
            img_dt = item['properties']['datetime'][:10]

            # 2. Build URL del NDVI cropped
            params = urllib.parse.urlencode({
                'collection': 'sentinel-2-l2a',
                'item': item_id,
                'assets': 'B08',
                'asset_as_band': 'true',
                'expression': '(B08-B04)/(B08+B04)',
                'rescale': '-0.2,0.8',
                'colormap_name': 'rdylgn',
            }, doseq=False)
            # assets debe aparecer dos veces (B08 + B04) — agrego manualmente
            params += '&assets=B04'
            ndvi_url = (f"https://planetarycomputer.microsoft.com/api/data/v1/item/bbox/"
                        f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}/600x600.png?{params}")
            ndvi_req = urllib.request.Request(ndvi_url, headers={"User-Agent": "AppAgua/1.0"})
            with urllib.request.urlopen(ndvi_req, timeout=30, context=_topo_ctx) as _resp:
                ndvi_bytes = _resp.read()
            ndvi_b64 = _b64.b64encode(ndvi_bytes).decode('ascii')
            ndvi_meta = {
                'date': img_dt,
                'cloud_cover': round(cloud, 1),
                'platform': 'Sentinel-2 L2A',
                'pixel_resolution_m': 10,
            }
    except Exception as e:
        print(f"[warn] NDVI no disponible: {e}", file=sys.stderr)

    # 12) Mapa de la parcela con contexto (CARTO Voyager tiles)
    map_b64 = None
    if MAP_AVAILABLE:
        try:
            # Markers de cuerpos de agua cercanos
            extra = []
            for wb_id, wb in water_bodies['water_bodies'].items():
                wlat, wlng = wb['coords']
                dist_km = math.hypot((wlat-lat)*111, (wlng-lng)*111*math.cos(math.radians(lat)))
                if dist_km <= 150:
                    extra.append({'lat': wlat, 'lng': wlng, 'label': wb['name_short'],
                                  'color': '#1565c0'})
            # Zoom según extensión de la parcela (más chica = más zoom)
            zoom = 10
            if area_ha and area_ha < 200: zoom = 12
            elif area_ha and area_ha < 800: zoom = 11
            map_b64 = render_to_base64(lat, lng, zoom=zoom, width=620, height=380,
                                        markers_extra=extra[:5])
        except Exception as e:
            print(f"[warn] Falló generación del mapa: {e}", file=sys.stderr)

    # 11) Recomendaciones por reglas (árbol de decisión simple)
    recommendations = []
    # Cultivos sugeridos
    if normal_91_20:
        if normal_91_20 >= 1100:
            recommendations.append(('Cultivos', 'Soja, maíz, trigo en rotación. Aptitud para arroz en partes bajas. Pasturas perennes viables todo el año.'))
        elif normal_91_20 >= 800:
            recommendations.append(('Cultivos', 'Soja y maíz con rendimientos estables. Trigo en rotación. Cebada/girasol como opciones de menor demanda hídrica.'))
        elif normal_91_20 >= 500:
            recommendations.append(('Cultivos', 'Trigo, cebada, girasol. Soja y maíz solo con riego complementario. Pastoreo extensivo apropiado.'))
        elif normal_91_20 >= 250:
            recommendations.append(('Cultivos', 'Producción de secano marginal. Riego obligatorio para horticultura/fruticultura. Pastoreo de baja carga.'))
        else:
            recommendations.append(('Cultivos', 'Sin riego de transferencia: solo ganadería de baja carga o forestal. Con riego: vid, olivo, frutales.'))
    # Seguro recomendado
    if drought_score >= 50:
        recommendations.append(('Seguro', 'Seguro paramétrico contra sequía recomendado (cobertura indexada a lluvia o NDVI). Cobertura multiriesgo tradicional suele ser cara o restrictiva en esta zona.'))
    elif drought_score >= 30:
        recommendations.append(('Seguro', 'Seguro multiriesgo agrícola estándar adecuado. Considerar add-on paramétrico de sequía para años extremos.'))
    else:
        recommendations.append(('Seguro', 'Seguro multiriesgo agrícola estándar suficiente.'))
    # Inversión hídrica
    if excess_score >= 50:
        recommendations.append(('Infraestructura', 'Drenajes y canalizaciones internas críticos. Evaluar bombas de evacuación. Considerar topografía detallada antes de planeo de siembra.'))
    elif drought_score >= 50:
        recommendations.append(('Infraestructura', 'Evaluar perforación de pozos para riego complementario. Estudio hidrogeológico recomendado antes de inversión.'))
    # Estudios complementarios
    studies = []
    if subjacent_aquifers:
        studies.append('estudio de calidad del agua subterránea (salinidad, nitratos)')
    if excess_score >= 40:
        studies.append('relevamiento topográfico con DEM de 1-2 m para mapa de susceptibilidad a anegamiento')
    studies.append('verificación de pluviómetro/estación meteorológica más cercana')
    recommendations.append(('Estudios sugeridos', ' · '.join(s.capitalize() for s in studies)))

    # ─── HTML ──────────────────────────────────────────────────────────────
    today_str = date.today().strftime('%d/%m/%Y')
    chirps_series_recent = list(zip(chirps['metadata']['years'][-15:],
                                     chirps_basin['data'][-15:])) if chirps_basin else []

    # SVG: barras de climatología mensual con normal vs 2024
    def monthly_chart(normal, actual_2024, w=520, h=140):
        months = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
        all_vals = [v for v in (normal or []) + (actual_2024 or []) if v is not None]
        if not all_vals: return ''
        mx = max(all_vals) * 1.1
        bar_w = (w - 60) / 12
        pad_l, pad_b = 32, 22
        plot_h = h - pad_b - 8
        bars = []
        for i in range(12):
            x = pad_l + i * bar_w
            n = normal[i] if normal and i < len(normal) and normal[i] is not None else 0
            a = actual_2024[i] if actual_2024 and i < len(actual_2024) and actual_2024[i] is not None else 0
            # Barra normal (gris)
            n_h = (n / mx) * plot_h
            bars.append(f'<rect x="{x+2:.1f}" y="{8+plot_h-n_h:.1f}" width="{bar_w/2-2:.1f}" height="{n_h:.1f}" fill="#90a4ae" opacity="0.7"/>')
            # Barra 2024 (azul/rojo según anomalía)
            a_h = (a / mx) * plot_h
            color = '#1565c0' if a >= n else '#c0392b'
            bars.append(f'<rect x="{x+bar_w/2:.1f}" y="{8+plot_h-a_h:.1f}" width="{bar_w/2-2:.1f}" height="{a_h:.1f}" fill="{color}" opacity="0.85"/>')
            # Mes label
            bars.append(f'<text x="{x+bar_w/2:.1f}" y="{h-6}" font-size="9" fill="#666" text-anchor="middle">{months[i]}</text>')
            # Valor normal sobre la barra (solo en barras altas)
            if n > mx * 0.3:
                bars.append(f'<text x="{x+bar_w/4:.1f}" y="{8+plot_h-n_h-3:.1f}" font-size="8" fill="#555" text-anchor="middle">{round(n)}</text>')
        # Y axis labels
        axes = ''
        for frac in [0.25, 0.5, 0.75, 1.0]:
            y = 8 + plot_h - frac * plot_h
            axes += f'<line x1="{pad_l}" y1="{y}" x2="{w-5}" y2="{y}" stroke="#e0e0e0" stroke-width="0.5"/>'
            axes += f'<text x="{pad_l-4}" y="{y+3}" font-size="8" fill="#888" text-anchor="end">{round(frac*mx)}</text>'
        # Legend
        legend = f'<g transform="translate({w-160},2)"><rect width="14" height="9" fill="#90a4ae" opacity="0.7"/><text x="18" y="8" font-size="9" fill="#444">Normal 91-20</text><rect x="80" y="0" width="14" height="9" fill="#1565c0" opacity="0.85"/><text x="98" y="8" font-size="9" fill="#444">2024</text></g>'
        return f'<svg viewBox="0 0 {w} {h}" width="100%" style="display:block">{axes}{"".join(bars)}{legend}</svg>'

    # mini SVG sparkline para la serie histórica
    def sparkline(series, w=520, h=80):
        if not series: return ''
        vals = [v for _, v in series]
        mn, mx = min(vals), max(vals)
        rg = mx - mn or 1
        pts = []
        for i, (y, v) in enumerate(series):
            x = (i / (len(series)-1)) * w
            yy = h - ((v - mn) / rg) * h * 0.85 - h*0.075
            pts.append(f"{x:.1f},{yy:.1f}")
        mean = sum(vals)/len(vals)
        mean_y = h - ((mean - mn) / rg) * h * 0.85 - h*0.075
        bars = ''.join(
            f'<rect x="{(i/(len(series)-1))*w - 6:.1f}" y="{h - ((v-mn)/rg)*h*0.85 - h*0.075:.1f}" '
            f'width="12" height="{((v-mn)/rg)*h*0.85:.1f}" fill="{"#e74c3c" if v < mean else "#1565c0"}" opacity="0.75"/>'
            for i, (y, v) in enumerate(series)
        )
        labels = ''.join(
            f'<text x="{(i/(len(series)-1))*w:.1f}" y="{h-2}" font-size="9" fill="#888" text-anchor="middle">{y%100:02d}</text>'
            for i, (y, _) in enumerate(series) if i % 2 == 0
        )
        return f'<svg viewBox="0 0 {w} {h}" width="100%" style="display:block">{bars}<line x1="0" y1="{mean_y:.1f}" x2="{w}" y2="{mean_y:.1f}" stroke="#444" stroke-dasharray="3,2" stroke-width="0.8"/>{labels}</svg>'

    place_name = f"{lat:.4f}°S, {abs(lng):.4f}°O"
    slug = f"{lat:.2f}_{lng:.2f}".replace('-','m').replace('.','p')

    html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>Reporte Hídrico — {place_name}</title>
<style>
  body{{margin:0;font-family:'Helvetica Neue',Arial,sans-serif;color:#1c2b3a;background:#f5f7fa;padding:24px;}}
  .doc{{max-width:820px;margin:0 auto;background:#fff;padding:36px 42px;box-shadow:0 4px 24px rgba(0,0,0,.06);border-radius:6px;}}
  h1{{color:#0d3a5c;font-size:22px;margin:0 0 4px;border-bottom:3px solid #1565c0;padding-bottom:10px;}}
  .subtitle{{color:#5a6b7c;font-size:13px;margin-bottom:24px;}}
  .meta{{display:flex;flex-wrap:wrap;gap:18px;margin:18px 0 28px;padding:14px;background:#f0f6fc;border-radius:6px;font-size:12px;}}
  .meta dt{{font-weight:700;color:#0d3a5c;display:inline;margin-right:4px;}}
  .meta dd{{display:inline;margin:0 14px 0 0;color:#1c2b3a;}}
  h2{{color:#0d3a5c;font-size:15px;margin:32px 0 10px;border-left:4px solid #1565c0;padding-left:10px;letter-spacing:.02em;text-transform:uppercase;}}
  table{{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0;}}
  td,th{{padding:7px 10px;text-align:left;border-bottom:1px solid #e6ecf2;}}
  th{{background:#f8fbfd;color:#5a6b7c;font-weight:600;font-size:11px;letter-spacing:.04em;text-transform:uppercase;}}
  td.val{{font-weight:600;color:#0d3a5c;text-align:right;}}
  .scorebox{{display:flex;gap:14px;margin:14px 0;}}
  .score{{flex:1;padding:18px;border-radius:6px;text-align:center;color:#fff;}}
  .score .lbl{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;opacity:.9;margin-bottom:4px;}}
  .score .num{{font-size:32px;font-weight:700;line-height:1;}}
  .score .clase{{font-size:11px;opacity:.95;margin-top:4px;}}
  .bar-bg{{background:#e8eef4;height:8px;border-radius:4px;overflow:hidden;margin-top:4px;}}
  .bar{{height:100%;border-radius:4px;}}
  .small{{font-size:11px;color:#7a8997;}}
  .footer{{margin-top:36px;padding-top:18px;border-top:1px solid #e6ecf2;font-size:10px;color:#94a0ac;line-height:1.6;}}
  .badge{{display:inline-block;padding:2px 8px;border-radius:99px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;}}
  .b-red{{background:#fce4e4;color:#c0392b;}} .b-orange{{background:#fff3e0;color:#e65100;}}
  .b-yellow{{background:#fffde7;color:#bf8f3a;}} .b-green{{background:#e8f5e9;color:#2e7d32;}}
  .b-blue{{background:#e3f2fd;color:#1565c0;}}
</style></head>
<body><div class="doc">

<h1>Reporte de Riesgo Hídrico</h1>
<div class="subtitle">Análisis hidrológico, climatológico y satelital para due diligence de campo</div>

<div class="meta">
  <span><dt>Ubicación:</dt><dd>{place_name}</dd></span>
  <span><dt>Cuenca:</dt><dd>{basin['name']}</dd></span>
  <span><dt>Región:</dt><dd>{basin.get('region','—')}</dd></span>
  {f'<span><dt>Sup. parcela:</dt><dd>{area_ha:,} ha</dd></span>' if area_ha else ''}
  {f'<span><dt>ID:</dt><dd>{parcel_id}</dd></span>' if parcel_id else ''}
  {f'<span><dt>Propietario:</dt><dd>{owner}</dd></span>' if owner else ''}
  <span><dt>Generado:</dt><dd>{today_str}</dd></span>
</div>

{f'''<div style="margin:18px 0 8px;border:1px solid #d0dce8;border-radius:6px;overflow:hidden;line-height:0;">
  <img src="data:image/png;base64,{map_b64}" alt="Mapa de ubicación" style="display:block;width:100%;"/>
</div>
<div class="small" style="margin-bottom:18px">📍 Campo (rojo) · 🔵 Cuerpos de agua relevantes en 150 km · Tiles © OpenStreetMap contributors, estilo CARTO Voyager</div>
''' if map_b64 else ''}

<h2>1 · Ubicación hídrica</h2>
<table>
  <tr><td>Cuenca hidrográfica</td><td class="val">{basin['name']}</td></tr>
  <tr><td>Superficie cuenca</td><td class="val">{basin.get('area_km2', 0):,} km²</td></tr>
  <tr><td>Status overall cuenca</td><td class="val"><span class="badge b-{'red' if basin['status']['overall']=='red' else 'yellow' if basin['status']['overall']=='yellow' else 'green'}">{basin['status']['overall'].upper()}</span></td></tr>
  <tr><td>Cuerpos de agua relevantes en 250 km</td><td class="val">{len(nearby_bodies)}</td></tr>
</table>

<h2>2 · Climatología histórica (1991–2020)</h2>
<table>
  <tr><td>Lluvia anual media (NASA POWER, bilinear)</td><td class="val">{round(normal_91_20) if normal_91_20 else '—'} mm</td></tr>
  <tr><td>Coeficiente de variación (CHIRPS cuenca)</td><td class="val">{cv*100:.0f} %</td></tr>
  <tr><td>Década más húmeda</td><td class="val">{wettest_decade[0]}–{wettest_decade[0]+9} ({round(wettest_decade[1])} mm/año)</td></tr>
  <tr><td>Década más seca</td><td class="val">{driest_decade[0]}–{driest_decade[0]+9} ({round(driest_decade[1])} mm/año)</td></tr>
  <tr><td>Años con sequía extrema (z &lt; -1.5σ)</td><td class="val">{extreme_dry} de {len(chirps_basin['data']) if chirps_basin else '—'}</td></tr>
  <tr><td>Años con exceso extremo (z &gt; +1.5σ)</td><td class="val">{extreme_wet} de {len(chirps_basin['data']) if chirps_basin else '—'}</td></tr>
</table>

<h2>3 · Distribución mensual de lluvia (1991–2020 vs 2024)</h2>
{monthly_chart(monthly_normal, monthly_2024)}
<table style="margin-top:8px">
  <tr><td>Trimestre más lluvioso (Dic–Feb)</td><td class="val">{round(wet_q)}% del total anual</td></tr>
  <tr><td>Trimestre más seco (Jun–Ago)</td><td class="val">{round(dry_q)}% del total anual</td></tr>
  <tr><td>Mes más lluvioso (normal)</td><td class="val">{['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'][wet_month_idx] if wet_month_idx is not None else '—'} ({round(monthly_normal[wet_month_idx]) if wet_month_idx is not None else '—'} mm)</td></tr>
  <tr><td>Mes más seco (normal)</td><td class="val">{['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'][dry_month_idx] if dry_month_idx is not None else '—'} ({round(monthly_normal[dry_month_idx]) if dry_month_idx is not None else '—'} mm)</td></tr>
</table>

<h2>4 · Serie histórica 2010–2024 (CHIRPS · {basin['name']})</h2>
{sparkline(chirps_series_recent)}
<div class="small">Barras azules = año con lluvia sobre la media · Barras rojas = año bajo la media · Línea punteada = media histórica {chirps_basin['mean_base'] if chirps_basin else '—'} mm</div>

<h2>5 · Comparables: posición en la cuenca</h2>
<table>
  <tr><td>Lluvia media de esta parcela</td><td class="val">{round(normal_91_20) if normal_91_20 else '—'} mm</td></tr>
  <tr><td>Lluvia media de la cuenca {basin['name']}</td><td class="val">{round(cuenca_mean) if cuenca_mean else '—'} mm</td></tr>
  <tr><td>Diferencia vs cuenca</td><td class="val" style="color:{'#2e7d32' if (diff_pct or 0) > 5 else '#c62828' if (diff_pct or 0) < -5 else '#1c2b3a'}">{('+' if (diff_pct or 0) > 0 else '') + f'{diff_pct:.1f} %' if diff_pct is not None else '—'}</td></tr>
  <tr><td>Percentil dentro de la cuenca</td><td class="val">{round(percentile_in_basin) if percentile_in_basin is not None else '—'} (0 = más seco, 100 = más húmedo)</td></tr>
</table>
<div class="small">Comparación calculada sobre {len(cuenca_points) if cuenca_points else 0} puntos NASA POWER dentro de la cuenca.</div>

<h2>6 · Frecuencia histórica de sequías</h2>
<table>
  <tr><th>Categoría</th><th>Eventos 1981–2024</th><th>Frecuencia esperada</th></tr>
  <tr><td>Sequía moderada (−1.0 a −1.5σ)</td><td class="val">{droughts_mild}</td><td class="val">{f"1 cada {round(len(series)/droughts_mild)} años" if (chirps_basin and droughts_mild > 0) else '—'}</td></tr>
  <tr><td>Sequía severa (−1.5 a −2.0σ)</td><td class="val">{droughts_severe}</td><td class="val">{f"1 cada {round(len(series)/droughts_severe)} años" if (chirps_basin and droughts_severe > 0) else '—'}</td></tr>
  <tr><td>Sequía extrema (≤ −2.0σ)</td><td class="val">{droughts_extreme}</td><td class="val">{f"1 cada {round(len(series)/droughts_extreme)} años" if (chirps_basin and droughts_extreme > 0) else '—'}</td></tr>
  <tr><td>Última sequía severa o peor</td><td class="val" colspan="2">{last_severe_year if last_severe_year else 'Sin eventos severos en el período'}</td></tr>
</table>

<h2>7 · Acuíferos subyacentes</h2>
{('<table><tr><th>Acuífero</th><th>Tipo</th><th>Prof. típica</th><th>Estado</th></tr>'
 + ''.join(f"""<tr><td><b>{a.get('name','?')}</b><div class='small'>{a.get('uses','—')}</div></td><td>{a.get('type','—')}</td><td class='val'>{a.get('depth_m','—')} m</td><td><span class='badge b-{a.get('status','green')}'>{a.get('status_label','—')}</span></td></tr>""" for a in subjacent_aquifers)
 + '</table>'
 + (f"<div class='small' style='margin-top:6px'>{subjacent_aquifers[0].get('status_detail','')}</div>" if subjacent_aquifers and subjacent_aquifers[0].get('status_detail') else '')
 ) if subjacent_aquifers else '<div class="small">No hay acuíferos importantes mapeados directamente bajo esta coordenada. Puede haber freática local; recomendado pozo de prueba para confirmar.</div>'}

<h2>8 · Topografía y susceptibilidad a anegamiento</h2>
{f'''<table>
  <tr><td>Elevación de la parcela (centro)</td><td class="val">{topo["field_elev"]} m s.n.m.</td></tr>
  <tr><td>Elevación media del entorno (2 km radio)</td><td class="val">{topo["mean_neigh"]} m</td></tr>
  <tr><td>Posición relativa de la parcela</td><td class="val" style="color:{'#2e7d32' if topo["relative"] > 1.5 else '#c62828' if topo["relative"] < -1.5 else '#1c2b3a'}">{('+' if topo["relative"] > 0 else '')}{topo["relative"]:.1f} m vs entorno</td></tr>
  <tr><td>Rango altimétrico (entorno 2 km)</td><td class="val">{topo["range"]} m</td></tr>
  <tr><td>Pendiente regional estimada</td><td class="val">{topo["slope"]} m/km</td></tr>
  <tr><td>Diagnóstico topográfico</td><td class="val">{topo["diagnostic"]}</td></tr>
  <tr><td>Susceptibilidad a anegamiento</td><td class="val"><span class="badge b-{'red' if topo['flood_risk']=='Elevado' else 'yellow' if topo['flood_risk']=='Medio' else 'green'}">{topo["flood_risk"]}</span></td></tr>
</table>
<div class="small">Elevación de SRTM-30m vía Open-Elevation. Diagnóstico simplificado por diferencia con vecinos a 2 km; complementar con DEM detallado si el riesgo de anegamiento es crítico.</div>
''' if topo else '<div class="small">Datos de elevación temporalmente no disponibles.</div>'}

<h2>9 · Estado actual</h2>
<table>
  <tr><td>Lluvia 2024 (estimada NASA POWER)</td><td class="val">{round(annual_2024) if annual_2024 else '—'} mm</td></tr>
  <tr><td>Anomalía 2024 vs media 1991–2020</td><td class="val" style="color:{'#c62828' if anom_2024<-5 else '#2e7d32' if anom_2024>5 else '#1c2b3a'}">{'+'if anom_2024>0 else ''}{anom_2024:.0f} %</td></tr>
  <tr><td>Lluvia 2023</td><td class="val">{round(annual_2023) if annual_2023 else '—'} mm</td></tr>
  <tr><td>Estado hídrico actual (CONAE GPM-IMERG, drought monitor)</td><td class="val"><a href="https://geoservicios2.conae.gov.ar/geoserver/EstatusHidrico/wms?REQUEST=GetMap&LAYERS=MHS_GPMIMERG_PCNTLAPI_1&BBOX={lng-3},{lat-3},{lng+3},{lat+3}&WIDTH=400&HEIGHT=400&FORMAT=image/png&SRS=EPSG:4326&VERSION=1.1.1" target="_blank">Ver mapa →</a></td></tr>
  <tr><td>Humedad de suelo SAOCOM últimos 7 días</td><td class="val"><a href="https://geoservicios3.conae.gov.ar/geoserver/HumedadDeSuelos/wms?REQUEST=GetMap&LAYERS=DSS_MSMKR_1&BBOX={lng-3},{lat-3},{lng+3},{lat+3}&WIDTH=400&HEIGHT=400&FORMAT=image/png&SRS=EPSG:4326&VERSION=1.1.1" target="_blank">Ver mapa →</a></td></tr>
</table>

{f'''<h2>10 · Productividad satelital reciente (NDVI Sentinel-2)</h2>
<table>
  <tr><td>Fecha de la imagen</td><td class="val">{ndvi_meta['date']}</td></tr>
  <tr><td>Plataforma</td><td class="val">{ndvi_meta['platform']}</td></tr>
  <tr><td>Cobertura nubosa</td><td class="val">{ndvi_meta['cloud_cover']:.1f}%</td></tr>
  <tr><td>Resolución espacial</td><td class="val">{ndvi_meta['pixel_resolution_m']} m / pixel</td></tr>
</table>
<div style="margin:12px 0;border:1px solid #d0dce8;border-radius:6px;overflow:hidden;line-height:0;display:flex;justify-content:center;">
  <img src="data:image/png;base64,{ndvi_b64}" alt="NDVI Sentinel-2" style="display:block;max-width:100%;"/>
</div>
<div class="small">
  <b>NDVI = (NIR − Red) / (NIR + Red)</b> · valores típicos: rojo/amarillo ~0 (suelo desnudo, agua), verde claro 0.3-0.5 (vegetación rala), verde oscuro 0.6-0.9 (vegetación densa, cultivos en pleno crecimiento). El cuadrante muestra ~5 × 5 km centrados en la parcela; el patrón cuadricular típico de la pampa permite identificar los campos colindantes.
</div>
''' if ndvi_b64 else '<h2>10 · Productividad satelital reciente (NDVI Sentinel-2)</h2><div class="small">Imagen Sentinel-2 cloud-free temporalmente no disponible para esta zona en los últimos 90 días.</div>'}

<h2>11 · Tendencia 1981–2024</h2>
<table>
  <tr><td>Pendiente lineal de lluvia anual</td><td class="val" style="color:{'#c62828' if slope<-1 else '#2e7d32' if slope>1 else '#1c2b3a'}">{'+'if slope>0 else ''}{slope:.2f} mm/año</td></tr>
  <tr><td>Cambio acumulado 1981 → 2024</td><td class="val">{slope*43:+.0f} mm ({slope*43/(chirps_basin['mean_base'] if chirps_basin else 1)*100:+.0f}%)</td></tr>
  <tr><td>Correlación temporal (r²)</td><td class="val">{r**2:.2f}</td></tr>
  <tr><td>Diagnóstico</td><td class="val">{'Aridización moderada' if slope < -1 else 'Humidificación leve' if slope > 1 else 'Estable, alta variabilidad interanual'}</td></tr>
</table>

<h2>12 · Proyecciones a futuro (CMIP6 · IPCC AR6)</h2>
{f'''<table>
  <tr><th>Variable</th><th>Histórico 1995–2014</th><th>2040–2059 SSP2-4.5</th><th>2040–2059 SSP5-8.5</th><th>2080–2099 SSP2-4.5</th><th>2080–2099 SSP5-8.5</th></tr>
  <tr>
    <td>Precipitación anual</td>
    <td class="val">{round(cmip["pr_baseline"])} mm</td>
    <td class="val" style="color:{'#1565c0' if cmip['delta_pr_2040_ssp245_pct'] > 0 else '#c62828'}">{round(cmip["pr_2040-2059_ssp245"])} mm <span class='small'>({'+'if cmip['delta_pr_2040_ssp245_pct']>0 else ''}{cmip['delta_pr_2040_ssp245_pct']:.1f}%)</span></td>
    <td class="val" style="color:{'#1565c0' if cmip['delta_pr_2040_ssp585_pct'] > 0 else '#c62828'}">{round(cmip["pr_2040-2059_ssp585"])} mm <span class='small'>({'+'if cmip['delta_pr_2040_ssp585_pct']>0 else ''}{cmip['delta_pr_2040_ssp585_pct']:.1f}%)</span></td>
    <td class="val" style="color:{'#1565c0' if cmip['delta_pr_2080_ssp245_pct'] > 0 else '#c62828'}">{round(cmip["pr_2080-2099_ssp245"])} mm <span class='small'>({'+'if cmip['delta_pr_2080_ssp245_pct']>0 else ''}{cmip['delta_pr_2080_ssp245_pct']:.1f}%)</span></td>
    <td class="val" style="color:{'#1565c0' if cmip['delta_pr_2080_ssp585_pct'] > 0 else '#c62828'}">{round(cmip["pr_2080-2099_ssp585"])} mm <span class='small'>({'+'if cmip['delta_pr_2080_ssp585_pct']>0 else ''}{cmip['delta_pr_2080_ssp585_pct']:.1f}%)</span></td>
  </tr>
  <tr>
    <td>Temperatura media</td>
    <td class="val">{cmip["tas_baseline"]:.1f} °C</td>
    <td class="val" style="color:#c62828">{cmip["tas_2040-2059_ssp245"]:.1f} °C <span class='small'>(+{cmip['delta_tas_2040_ssp245_C']:.1f})</span></td>
    <td class="val" style="color:#c62828">{cmip["tas_2040-2059_ssp585"]:.1f} °C <span class='small'>(+{cmip['delta_tas_2040_ssp585_C']:.1f})</span></td>
    <td class="val" style="color:#c62828">{cmip["tas_2080-2099_ssp245"]:.1f} °C <span class='small'>(+{cmip['delta_tas_2080_ssp245_C']:.1f})</span></td>
    <td class="val" style="color:#c62828">{cmip["tas_2080-2099_ssp585"]:.1f} °C <span class='small'>(+{cmip['delta_tas_2080_ssp585_C']:.1f})</span></td>
  </tr>
</table>
<div class="small" style="margin-top:6px">
  <b>Lectura:</b> El cambio de precipitación promedio a nivel país es moderado, pero su distribución espacial cambia significativamente (NE más húmedo, NO/Patagonia más seco) — la cifra nacional puede no reflejar tu zona específica.
  <b>Lo más cierto y consequential es la temperatura</b>: un aumento de +{cmip['delta_tas_2080_ssp585_C']:.1f}°C a 2080 implica ~10-20% más evapotranspiración, equivalente a perder ~100-150 mm de "lluvia útil" anual incluso si la lluvia total no baja.
  <br><b>Resolución:</b> Promedio Argentina (CMIP6 0.25° ensemble). Para downscaling sub-nacional, consultar IPCC AR6 Regional Atlas o solicitar análisis personalizado.
</div>
''' if cmip else '<div class="small">Proyecciones futuras no disponibles.</div>'}

<h2>13 · Caudal y cuerpos de agua cercanos</h2>
{'<table><tr><th>Métrica caudal cuenca</th><th>Media histórica</th><th>Último valor</th><th>Estado</th></tr>'+f'<tr><td>{flow_metric["label"]}</td><td class="val">{flow_metric["historical_mean"]} {flow_metric["unit"]}</td><td class="val">{flow_metric["data"][-1]["value"]} {flow_metric["unit"]}</td><td><span class="badge b-{"red" if flow_metric["data"][-1]["value"] < flow_metric["historical_mean"]*0.7 else "yellow" if flow_metric["data"][-1]["value"] < flow_metric["historical_mean"]*0.9 else "green"}">{flow_metric["data"][-1]["year"]}</span></td></tr></table>' if flow_metric else '<div class="small">Sin estación de caudal medida en esta cuenca</div>'}

<table>
  <tr><th>Cuerpo de agua</th><th>Dist.</th><th>Tipo</th><th>Tendencia</th><th>Crecim. 10a</th></tr>
  {''.join(f'<tr><td>{b["name"]}</td><td class="val">{b["dist_km"]} km</td><td>{b["type"]}</td><td><span class="badge b-{"red" if b["trend"]=="critico" else "orange" if b["trend"]=="descendente" else "blue" if b["trend"]=="variable" else "green"}">{b["trend"]}</span></td><td class="val" style="color:{"#c62828" if b["growth_pct"]>30 else "#2e7d32" if b["growth_pct"]<-30 else "#1c2b3a"}">{"+"if b["growth_pct"]>0 else ""}{b["growth_pct"]}%</td></tr>' for b in nearby_bodies[:6])}
</table>

<h2>14 · Score de riesgo hídrico</h2>
<div class="scorebox">
  <div class="score" style="background:{color_for_score(drought_score)}">
    <div class="lbl">Riesgo de sequía</div>
    <div class="num">{drought_score}</div>
    <div class="clase">{color_class(drought_score)}</div>
  </div>
  <div class="score" style="background:{color_for_score(excess_score)}">
    <div class="lbl">Riesgo de exceso</div>
    <div class="num">{excess_score}</div>
    <div class="clase">{color_class(excess_score)}</div>
  </div>
  <div class="score" style="background:{color_for_score(composite)}">
    <div class="lbl">Riesgo agregado</div>
    <div class="num">{composite}</div>
    <div class="clase">{color_class(composite)}</div>
  </div>
</div>
<div class="small">Score 0–100. Sequía: combina aridización (tendencia), variabilidad interanual (CV) y estado actual (anomalía). Exceso: combina años de lluvia extrema (z&gt;+1.5) y cuerpos de agua cercanos en crecimiento &gt;30% en 10 años. Score agregado: 55% sequía + 45% exceso (calibración inicial — ajustable por uso).</div>

<h2>15 · Recomendaciones técnicas</h2>
<table>
  {''.join(f'<tr><td style="width:140px;vertical-align:top"><b>{label}</b></td><td>{text}</td></tr>' for label, text in recommendations)}
</table>

<h2>16 · Lectura ejecutiva</h2>
<div style="background:#f0f6fc;padding:16px;border-radius:6px;font-size:13px;line-height:1.6">
{
  'El campo se ubica en una zona con <b>tendencia a aridización moderada</b> y alta variabilidad interanual típica de la pampa deprimida. ' if slope < -1 else
  'El campo se ubica en una zona con <b>régimen pluvial estable</b> en las últimas 4 décadas. '
}
{
  f'La lluvia 2024 estuvo <b>{abs(round(anom_2024))}% por debajo</b> de la media climatológica, en línea con el evento La Niña reciente. ' if anom_2024 < -5 else
  f'La lluvia 2024 estuvo <b>{round(anom_2024):+}%</b> respecto a la media climatológica, dentro del rango normal. '
}
{
  f'Hay {growing_count} cuerpo(s) de agua cercanos con crecimiento sostenido &gt;30% que pueden incrementar el riesgo de anegamiento en suelos bajos del partido. ' if growing_count > 0 else
  'No se detectan cuerpos de agua cercanos en crecimiento crítico. '
}
El score agregado de <b>{composite}/100 ({color_class(composite).lower()})</b> sitúa al campo en {('riesgo manejable con prácticas estándar' if composite < 40 else 'riesgo que justifica plan de manejo y/o seguro paramétrico complementario' if composite < 60 else 'riesgo elevado que requiere análisis de viabilidad y cobertura específica antes de la operación')}.
</div>

<div class="footer">
<b>Fuentes:</b> Cuencas HydroBASINS L5 (HydroSHEDS) · Climatología NASA POWER (MERRA-2) 1991–2024 · Serie histórica CHIRPS v2.0 (CHC/UCSB) · Caudal histórico INA · Cuerpos de agua JRC Global Surface Water (Landsat) · Estado actual CONAE (GPM-IMERG, SAOCOM).
<br><br>
<b>Limitaciones:</b> Climatología basada en grilla 2°×2° con interpolación bilineal; valores son representativos de un radio de 100-200 km. La serie CHIRPS se promedia sobre toda la cuenca, no parcela. Los scores son orientativos y no reemplazan análisis de suelo, topografía detallada ni proyecciones de cambio climático regionalizadas. Recomendado complementar con visita técnica y datos puntuales (ej. pozo de freática, estación pluviométrica del partido).
<br><br>
Reporte generado por <b>App Agua</b> · {today_str} · v1.0 prototipo
</div>

</div></body></html>"""

    out_path = Path('/tmp') / f"land_report_{slug}.html"
    out_path.write_text(html)
    return out_path, {
        'basin': basin['name'],
        'normal_91_20': round(normal_91_20) if normal_91_20 else None,
        'annual_2024': round(annual_2024) if annual_2024 else None,
        'anom_2024_pct': round(anom_2024, 1),
        'slope_mm_year': round(slope, 2),
        'drought_score': drought_score,
        'excess_score': excess_score,
        'composite_score': composite,
        'nearby_bodies': len(nearby_bodies),
        'growing_bodies': growing_count,
    }


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--lat', type=float, required=True)
    p.add_argument('--lng', type=float, required=True)
    p.add_argument('--area-ha', type=float, default=None)
    p.add_argument('--owner', default=None)
    p.add_argument('--parcel-id', default=None)
    args = p.parse_args()

    out, summary = generate(args.lat, args.lng, args.area_ha, args.owner, args.parcel_id)
    print(f"✓ Reporte generado: {out}")
    print(f"  abrí en browser: open {out}")
    print()
    for k, v in summary.items():
        print(f"  {k:20s}  {v}")
