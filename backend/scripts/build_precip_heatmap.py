"""
build_precip_heatmap.py
Grilla de precipitación para el heatmap — fuente: NASA POWER API (MERRA-2).

NASA POWER ofrece precipitación mensual corregida (PRECTOTCORR, mm/día).
Se convierte a mm/mes multiplicando por días del mes.
Cubre 1981–presente, sin auth, accesible mundialmente.

Uso: python3 build_precip_heatmap.py
Salida: backend/data/precip_heatmap.json  (~80 KB)
"""

import json, time, urllib.request, urllib.parse, calendar, ssl
from datetime import date
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Script local de generación de datos

# ── Grilla 2° sobre Argentina ─────────────────────────────────────────────
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX, STEP = -55.0, -21.0, -74.0, -52.0, 2.0
lats = [round(LAT_MIN + i * STEP, 1) for i in range(int((LAT_MAX - LAT_MIN) / STEP) + 1)]
lons = [round(LON_MIN + j * STEP, 1) for j in range(int((LON_MAX - LON_MIN) / STEP) + 1)]
POINTS = [(la, lo) for la in lats for lo in lons]
print(f"Grilla {STEP}°: {len(lats)} lats × {len(lons)} lons = {len(POINTS)} puntos")

TODAY = date.today()

# ── Fetch NASA POWER ───────────────────────────────────────────────────────
BASE = "https://power.larc.nasa.gov/api/temporal/monthly/point"

def fetch_power(lat, lon, start_yr, end_yr):
    params = urllib.parse.urlencode({
        "parameters": "PRECTOTCORR",
        "community": "RE",
        "longitude": lon, "latitude": lat,
        "start": start_yr, "end": end_yr,
        "format": "JSON",
    })
    ctx = ssl._create_unverified_context()   # por-thread, evita problemas de concurrencia
    for attempt in range(3):
        try:
            with urllib.request.urlopen(f"{BASE}?{params}", timeout=25, context=ctx) as r:
                return json.loads(r.read())
        except Exception as e:
            if attempt == 2:
                return None
            time.sleep(2)

def mm_per_day_to_monthly(raw: dict) -> dict:
    """
    raw: {'202201': 5.03, '202202': 4.62, ...}  (mm/día)
    → {'2022': [mm_ene, mm_feb, ...], ...}
    """
    by_year = {}
    for ym, val in raw.items():
        if val is None or val < 0:
            continue
        yr, mo = ym[:4], int(ym[4:])
        if mo > 12:          # mes 13 = total anual de la API, ignorar
            continue
        days = calendar.monthrange(int(yr), mo)[1]
        by_year.setdefault(yr, [None]*12)
        by_year[yr][mo-1] = round(val * days, 1)
    return by_year

def process(lat, lon):
    # 1991–2020 para climatología, 2021–2024 para anomalías recientes
    d_clm = fetch_power(lat, lon, 1991, 2020)
    d_rec = fetch_power(lat, lon, 2021, 2024)

    if not d_clm or not d_rec:
        return None

    raw_clm = d_clm.get("properties", {}).get("parameter", {}).get("PRECTOTCORR", {})
    raw_rec = d_rec.get("properties", {}).get("parameter", {}).get("PRECTOTCORR", {})

    if not raw_clm or not raw_rec:
        return None

    monthly_clm = mm_per_day_to_monthly(raw_clm)  # 1991-2020 por año
    monthly_rec = mm_per_day_to_monthly(raw_rec)   # 2021-2024 por año

    # Climatología: media de sumas anuales 1991-2020
    annual_sums_clm = []
    for yr, months in monthly_clm.items():
        s = sum(m for m in months if m is not None)
        if s > 5:
            annual_sums_clm.append(s)
    if not annual_sums_clm:
        return None
    normal_91_20 = round(sum(annual_sums_clm) / len(annual_sums_clm))

    # Climatología mensual 1991-2020 (promedio mes a mes)
    monthly_normal = [0.0] * 12
    monthly_count  = [0] * 12
    for yr, months in monthly_clm.items():
        for i, v in enumerate(months):
            if v is not None and v >= 0:
                monthly_normal[i] += v
                monthly_count[i] += 1
    monthly_normal_91_20 = [round(monthly_normal[i] / monthly_count[i], 1) if monthly_count[i] > 0 else 0
                            for i in range(12)]

    # Anual reciente
    annual = {}
    for yr, months in monthly_rec.items():
        s = sum(m for m in months if m is not None)
        if s > 0:
            annual[yr] = round(s)

    if not annual:
        return None

    # Anomalía 2024
    ann_2024    = annual.get("2024")
    anom_2024   = round(ann_2024 - normal_91_20) if ann_2024 else None
    anom_pct    = round(anom_2024 / normal_91_20 * 100) if (anom_2024 and normal_91_20) else None

    return {
        "lat": lat, "lon": lon,
        "normal_91_20":         normal_91_20,
        "monthly_normal_91_20": monthly_normal_91_20,
        "annual":               annual,
        "monthly_2024":   [round(v) if v else 0 for v in (monthly_rec.get("2024") or [0]*12)],
        "monthly_2023":   [round(v) if v else 0 for v in (monthly_rec.get("2023") or [0]*12)],
        "anom_2024_mm":   anom_2024,
        "anom_2024_pct":  anom_pct,
    }

# ── Main ──────────────────────────────────────────────────────────────────
results = []
n = len(POINTS)
print(f"\nFetcheando {n} puntos (NASA POWER 1991-2024, 6 hilos)…\n")

with ThreadPoolExecutor(max_workers=6) as ex:
    futures = {ex.submit(process, la, lo): (la, lo) for la, lo in POINTS}
    done = 0
    for fut in as_completed(futures):
        la, lo = futures[fut]
        done  += 1
        try:
            res = fut.result()
        except Exception as e:
            print(f"  EXCEPTION ({la},{lo}): {type(e).__name__}: {e}")
            res = None
        if res:
            results.append(res)
            print(f"  [{done:3d}/{n}] ({la:6.1f},{lo:6.1f})  "
                  f"norm={res['normal_91_20']:4d}mm  2024={res['annual'].get('2024','?')}mm  "
                  f"anom={res['anom_2024_pct']}%")
        else:
            print(f"  [{done:3d}/{n}] ({la:6.1f},{lo:6.1f})  —")

# ── Guardar ──────────────────────────────────────────────────────────────
out = {
    "metadata": {
        "source": "NASA POWER v9 (MERRA-2 reanalysis, 0.5°×0.625° nativo)",
        "unit": "mm",
        "grid_step_deg": STEP,
        "period": "1991–2024",
        "generated": TODAY.isoformat(),
        "note": "Grilla 2° sobre Argentina. Climatología base 1991–2020. "
                "Precipitación corregida (PRECTOTCORR). "
                "Anual 2024 puede ser parcial según fecha de generación.",
    },
    "points": sorted(results, key=lambda p: (-p["lat"], p["lon"])),
}

out_path = Path(__file__).parent.parent / "data" / "precip_heatmap.json"
with open(out_path, "w") as f:
    json.dump(out, f, ensure_ascii=False, separators=(',', ':'))

size_kb = out_path.stat().st_size / 1024
print(f"\n✓ {len(results)} puntos → {out_path.name}  ({size_kb:.0f} KB)")
if results:
    norms = [p["normal_91_20"] for p in results]
    print(f"  Climatología: min={min(norms)}  max={max(norms)}  media={round(sum(norms)/len(norms))} mm/año")
