#!/usr/bin/env python3
"""
build_precip_grid.py — Pre-computa el grid de precipitación ERA5 para Argentina.

Genera: backend/data/ar_precip_grid.json

Fuente: ERA5 reanalysis via Open-Meteo archive API (sin clave, gratuito)
Resolución: 0.5° (~55 km) — igual al grid nativo de ERA5

Uso:
    cd backend/scripts
    python3 build_precip_grid.py

Tiempo estimado: 1-3 horas (depende de la latencia a Open-Meteo)
El script guarda progreso cada 50 celdas — si se interrumpe, retoma desde ahí.

Requisitos: solo librería estándar Python 3.8+
"""

import json
import math
import os
import ssl
import time
import urllib.parse
import urllib.request

# ── configuración ──────────────────────────────────────────────────────────────
GRID_RES    = 1.0          # resolución en grados (debe coincidir con main.py)
START_YEAR  = 1990
END_YEAR    = 2025
SLEEP_S     = 1.0          # pausa entre llamadas (respetar rate limit Open-Meteo)
MAX_RETRIES = 5            # reintentos ante 429 (con backoff exponencial)
SAVE_EVERY  = 50           # guardar progreso cada N celdas
MIN_DAYS    = 300          # mínimo de días por año para considerarlo válido

# Bounding box de Argentina continental + Patagonia (excluye Antártida)
LAT_MIN, LAT_MAX = -55.0, -21.5
LON_MIN, LON_MAX = -73.5, -53.0

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "ar_precip_grid.json")
PROG_PATH = OUT_PATH + ".progress"   # archivo de progreso parcial

# ── SSL ────────────────────────────────────────────────────────────────────────
_SSL_CTX = ssl.create_default_context()
try:
    _SSL_CTX.load_verify_locations("/etc/ssl/cert.pem")
except Exception:
    pass  # en Linux el bundle por defecto funciona bien


def grid_cells():
    """Genera todas las celdas (lat, lon) del bounding box de Argentina."""
    cells = []
    lat = LAT_MIN
    while lat <= LAT_MAX + 0.01:
        lon = LON_MIN
        while lon <= LON_MAX + 0.01:
            cells.append((round(lat, 1), round(lon, 1)))
            lon = round(lon + GRID_RES, 1)
        lat = round(lat + GRID_RES, 1)
    return cells


_RATE_LIMITED = "RATE_LIMITED"   # sentinel para distinguir 429 de "sin datos"

def fetch_cell(lat: float, lon: float):
    """Llama a Open-Meteo ERA5 para una celda.
    Retorna dict con data anual, None si es océano/sin datos, o _RATE_LIMITED si 429."""
    params = urllib.parse.urlencode({
        "latitude":   lat,
        "longitude":  lon,
        "start_date": f"{START_YEAR}-01-01",
        "end_date":   f"{END_YEAR}-12-31",
        "daily":      "precipitation_sum",
        "timezone":   "UTC",
    })
    url = f"https://archive-api.open-meteo.com/v1/era5?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "AppAgua-GridBuilder/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as r:
            raw = json.loads(r.read())
    except Exception as e:
        msg = str(e)
        if "429" in msg:
            return _RATE_LIMITED
        print(f"  ERROR fetch ({lat},{lon}): {e}")
        return None

    times  = raw.get("daily", {}).get("time", [])
    values = raw.get("daily", {}).get("precipitation_sum", [])
    if not times:
        return None

    # agregar diario → anual
    by_year: dict[int, list] = {}
    for t, v in zip(times, values):
        year = int(t[:4])
        if v is not None:
            by_year.setdefault(year, []).append(v)

    annual = []
    for year in sorted(by_year):
        vals = by_year[year]
        if len(vals) >= MIN_DAYS:
            annual.append({"year": year, "value": round(sum(vals), 1)})

    if not annual:
        return None

    mean = round(sum(d["value"] for d in annual) / len(annual), 1)
    annual[-1]["is_current"] = True

    return {
        "lat":  raw.get("latitude", lat),
        "lng":  raw.get("longitude", lon),
        "mean": mean,
        "data": annual,
    }


def cell_key(lat: float, lon: float) -> str:
    return f"{lat:.1f}_{lon:.1f}"


def load_progress() -> dict:
    # 1. archivo de progreso parcial (de una corrida interrumpida)
    if os.path.exists(PROG_PATH):
        with open(PROG_PATH, encoding="utf-8") as f:
            return json.load(f)
    # 2. grid final ya existente → retomar solo los nulls (celdas que fallaron con 429)
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, encoding="utf-8") as f:
            existing = json.load(f).get("cells", {})
        # solo retener las celdas con datos reales; las null se reintentarán
        return {k: v for k, v in existing.items() if v is not None}
    return {}


def save_progress(cells_done: dict):
    with open(PROG_PATH, "w", encoding="utf-8") as f:
        json.dump(cells_done, f)


def finalize(cells_done: dict):
    output = {
        "metadata": {
            "title":       "Grid de precipitación anual ERA5 — Argentina",
            "source":      "ERA5 reanalysis via Open-Meteo (archive-api.open-meteo.com)",
            "resolution":  f"{GRID_RES}°",
            "period":      f"{START_YEAR}-{END_YEAR}",
            "generated":   time.strftime("%Y-%m-%d"),
            "cells":       len(cells_done),
            "note":        "Generado con build_precip_grid.py. No editar manualmente.",
        },
        "cells": cells_done,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, separators=(",", ":"))
    print(f"\nGrid guardado en {OUT_PATH} ({len(cells_done)} celdas)")
    if os.path.exists(PROG_PATH):
        os.remove(PROG_PATH)


def main():
    cells    = grid_cells()
    total    = len(cells)
    done     = load_progress()
    pending  = [(lat, lon) for lat, lon in cells if cell_key(lat, lon) not in done]

    print(f"Grid ERA5 Argentina — {GRID_RES}° resolución")
    print(f"Total celdas en bbox: {total} | Ya procesadas: {len(done)} | Pendientes: {len(pending)}")
    if not pending:
        print("Nada pendiente. Generando archivo final...")
        finalize(done)
        return

    errors = 0
    for i, (lat, lon) in enumerate(pending, 1):
        key = cell_key(lat, lon)
        print(f"[{len(done)+1}/{total}] ({lat:.1f}, {lon:.1f})", end=" ", flush=True)

        # reintentos con backoff exponencial ante 429
        result = None
        for attempt in range(MAX_RETRIES):
            result = fetch_cell(lat, lon)
            if result != _RATE_LIMITED:
                break
            wait = 30 * (2 ** attempt)   # 30s, 60s, 120s, 240s, 480s
            print(f"\n  429 rate limit — esperando {wait}s...", end=" ", flush=True)
            time.sleep(wait)

        if result and result != _RATE_LIMITED:
            done[key] = result
            print(f"✓  {result['mean']} mm/año  ({len(result['data'])} años)")
        elif result == _RATE_LIMITED:
            # agotados los reintentos — no guardar null, se reintentará en la próxima corrida
            print("✗ rate limit persistente, se reintentará")
            errors += 1
            continue   # no agregar al dict → la próxima corrida la reintenta
        else:
            # sin datos reales (océano / fuera de cobertura)
            done[key] = None
            print("— (sin datos / océano)")

        if i % SAVE_EVERY == 0:
            save_progress(done)
            print(f"  → Progreso guardado ({len(done)} celdas)")

        time.sleep(SLEEP_S)

    finalize(done)
    land_cells = sum(1 for v in done.values() if v is not None)
    print(f"Celdas con datos: {land_cells} / {total} (resto: océano o sin cobertura)")


if __name__ == "__main__":
    main()
