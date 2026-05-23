"""
build_precip_heatmap.py
Grilla de precipitación para el heatmap del mapa (Open-Meteo/ERA5-Land).

Estrategia:
 - Grilla 2° sobre Argentina (~130 puntos terrestres)
 - Fetch anual 2022-2024 en un solo request por punto (~1100 días)
 - Fetch mensual 2024 del mismo bloque de datos
 - Requests concurrentes (8 hilos) → termina en ~2-3 min
 - Sin climatología 30 años (demasiado pesado por punto)

Uso: python3 build_precip_heatmap.py
Salida: backend/data/precip_heatmap.json
"""

import json, time, urllib.request, urllib.parse, urllib.error
from datetime import date, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Grilla ────────────────────────────────────────────────────────────────
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX, STEP = -55.0, -21.0, -74.0, -52.0, 2.0

lats = [round(LAT_MIN + i * STEP, 1) for i in range(int((LAT_MAX - LAT_MIN) / STEP) + 1)]
lons = [round(LON_MIN + j * STEP, 1) for j in range(int((LON_MAX - LON_MIN) / STEP) + 1)]
POINTS = [(la, lo) for la in lats for lo in lons]

print(f"Grilla {STEP}°: {len(lats)} lats × {len(lons)} lons = {len(POINTS)} puntos")

# ── Fechas ────────────────────────────────────────────────────────────────
TODAY      = date.today()
END_DATE   = (TODAY - timedelta(days=6)).isoformat()
START_DATE = "2022-01-01"   # 3 años → requests manejables

# ── Fetch ─────────────────────────────────────────────────────────────────
BASE = "https://archive-api.open-meteo.com/v1/archive"

def fetch_point(lat, lon):
    params = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "start_date": START_DATE, "end_date": END_DATE,
        "daily": "precipitation_sum",
        "timezone": "UTC",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(f"{BASE}?{params}", timeout=25) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 400:   # fuera de dominio (océano, etc.)
                return None
            time.sleep(1.5 * (attempt + 1))
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None

def process(lat, lon):
    d = fetch_point(lat, lon)
    if not d or "daily" not in d:
        return None

    times = d["daily"]["time"]
    vals  = d["daily"]["precipitation_sum"]

    # Suma anual
    annual = {}
    for t, v in zip(times, vals):
        if v is None: continue
        y = t[:4]
        annual[y] = annual.get(y, 0.0) + v

    # Mensual 2024 y 2023
    monthly = {yr: [0.0] * 12 for yr in ("2024", "2023")}
    for t, v in zip(times, vals):
        if v is None: continue
        yr, mo = t[:4], int(t[5:7]) - 1
        if yr in monthly:
            monthly[yr][mo] += v

    # Filtro: si la suma anual total es 0 o casi (océano, nieve perpetua sin rain gauge)
    total = sum(annual.values())
    if total < 5:
        return None

    return {
        "lat": lat, "lon": lon,
        "annual":       {yr: round(v) for yr, v in annual.items()},
        "monthly_2024": [round(x) for x in monthly["2024"]],
        "monthly_2023": [round(x) for x in monthly["2023"]],
    }

# ── Concurrente ───────────────────────────────────────────────────────────
results = []
ok = err = 0

print(f"\nFetcheando {len(POINTS)} puntos ({START_DATE} → {END_DATE}) con 8 hilos…\n")

with ThreadPoolExecutor(max_workers=8) as ex:
    futures = {ex.submit(process, la, lo): (la, lo) for la, lo in POINTS}
    for i, fut in enumerate(as_completed(futures), 1):
        la, lo = futures[fut]
        try:
            res = fut.result()
        except Exception as e:
            res = None
        if res:
            results.append(res)
            ok += 1
            a24 = res["annual"].get("2024", "?")
            print(f"  [{i:3d}/{len(POINTS)}] ({la:6.1f}, {lo:6.1f})  2024={a24}mm")
        else:
            err += 1
            print(f"  [{i:3d}/{len(POINTS)}] ({la:6.1f}, {lo:6.1f})  —")

# ── Guardar ───────────────────────────────────────────────────────────────
out = {
    "metadata": {
        "source": "Open-Meteo Archive API (ERA5-Land reanalysis, 0.1° nativo)",
        "unit": "mm",
        "grid_step_deg": STEP,
        "period": f"{START_DATE} → {END_DATE}",
        "generated": TODAY.isoformat(),
        "note": "Grilla regular 2° sobre Argentina. Puntos oceánicos filtrados. "
                "ERA5-Land ~5-6 días de desfasaje respecto al tiempo real.",
    },
    "points": sorted(results, key=lambda p: (-p["lat"], p["lon"])),
}

out_path = Path(__file__).parent.parent / "data" / "precip_heatmap.json"
with open(out_path, "w") as f:
    json.dump(out, f, ensure_ascii=False, separators=(',', ':'))

size_kb = out_path.stat().st_size / 1024
print(f"\n✓ {ok} puntos guardados, {err} sin datos → {out_path.name}  ({size_kb:.0f} KB)")

if results:
    a24s = [p["annual"].get("2024", 0) for p in results if p["annual"].get("2024")]
    if a24s:
        print(f"  2024: min={min(a24s)}mm  max={max(a24s)}mm  media={round(sum(a24s)/len(a24s))}mm")
