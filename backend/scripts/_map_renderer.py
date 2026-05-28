"""
_map_renderer.py
Genera una imagen PNG centrada en (lat, lng) con marker del campo +
overlays de cuerpos de agua y cuencas cercanas.

Tiles: CARTO Voyager (CC BY 3.0, permite uso comercial). Sin API key.
Salida: bytes PNG, o data URI base64 para embeber en HTML.
"""

import math
import io
import base64
import ssl
import urllib.request
from PIL import Image, ImageDraw, ImageFont

TILE_PROVIDER = "https://basemaps.cartocdn.com/rastertiles/voyager"  # CC BY 3.0
TILE_SIZE = 256
USER_AGENT = "AppAgua/1.0 (LandReport; +https://app-agua-production.up.railway.app)"

# SSL context tolerante (algunos entornos no tienen el CA bundle completo).
# Tiles públicas, no hay riesgo de MITM relevante.
_SSL_CTX = ssl._create_unverified_context()

# ── proyección Web Mercator ───────────────────────────────────────────────
def deg_to_tile(lat, lng, zoom):
    """(lat, lng) → (xtile, ytile) en tile-grid (puede tener fracción)."""
    n = 2.0 ** zoom
    xtile = (lng + 180.0) / 360.0 * n
    yrad = math.radians(lat)
    ytile = (1.0 - math.log(math.tan(yrad) + 1 / math.cos(yrad)) / math.pi) / 2.0 * n
    return xtile, ytile

def tile_to_deg(xtile, ytile, zoom):
    n = 2.0 ** zoom
    lng = xtile / n * 360.0 - 180.0
    yrad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    lat = math.degrees(yrad)
    return lat, lng

def fetch_tile(z, x, y, retries=3):
    url = f"{TILE_PROVIDER}/{z}/{x}/{y}.png"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for _ in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as r:
                return Image.open(io.BytesIO(r.read())).convert('RGB')
        except Exception:
            continue
    # Tile faltante: gris claro
    return Image.new('RGB', (TILE_SIZE, TILE_SIZE), '#e8e8e8')

# ── renderer principal ────────────────────────────────────────────────────
def render_land_map(lat, lng, zoom=10, width=620, height=380,
                    markers_extra=None):
    """
    markers_extra: lista de dicts {'lat', 'lng', 'label', 'color'} para
    cuerpos de agua o referencias adicionales.
    """
    # Tile central en coords fraccionales
    xt_c, yt_c = deg_to_tile(lat, lng, zoom)

    # Cuántos tiles necesitamos a cada lado del centro
    tiles_w = math.ceil(width / TILE_SIZE) + 1
    tiles_h = math.ceil(height / TILE_SIZE) + 1

    # Origen del canvas grande (esquina sup-izq) en tile-units
    x0 = xt_c - tiles_w / 2
    y0 = yt_c - tiles_h / 2

    big_w = tiles_w * TILE_SIZE
    big_h = tiles_h * TILE_SIZE
    canvas = Image.new('RGB', (big_w, big_h), '#fff')

    for dx in range(tiles_w):
        for dy in range(tiles_h):
            xi = int(math.floor(x0)) + dx
            yi = int(math.floor(y0)) + dy
            n = 2 ** zoom
            if xi < 0 or xi >= n or yi < 0 or yi >= n:
                continue
            tile_img = fetch_tile(zoom, xi, yi)
            # Posición en el canvas grande
            px = int((xi - x0) * TILE_SIZE)
            py = int((yi - y0) * TILE_SIZE)
            canvas.paste(tile_img, (px, py))

    # Recortar al tamaño final centrado en el campo
    cx_in_big = (xt_c - x0) * TILE_SIZE
    cy_in_big = (yt_c - y0) * TILE_SIZE
    left = int(cx_in_big - width / 2)
    top  = int(cy_in_big - height / 2)
    final = canvas.crop((left, top, left + width, top + height))

    # ── overlays ───────────────────────────────────────────────────────────
    draw = ImageDraw.Draw(final, 'RGBA')

    def latlng_to_px(la, ln):
        xt, yt = deg_to_tile(la, ln, zoom)
        px = (xt - x0) * TILE_SIZE - left
        py = (yt - y0) * TILE_SIZE - top
        return px, py

    # Markers extra (cuerpos de agua, referencias)
    if markers_extra:
        for m in markers_extra:
            mx, my = latlng_to_px(m['lat'], m['lng'])
            if 0 <= mx <= width and 0 <= my <= height:
                color = m.get('color', '#1565c0')
                draw.ellipse([mx-5, my-5, mx+5, my+5], fill=color, outline='#fff', width=1)
                label = m.get('label', '')
                if label:
                    draw.text((mx + 8, my - 5), label, fill='#222')

    # Marker principal del campo (pin estilo)
    cx, cy = width // 2, height // 2
    # Pin: triángulo + círculo
    pin_color = '#c0392b'
    draw.ellipse([cx-12, cy-26, cx+12, cy-2], fill=pin_color, outline='#fff', width=2)
    draw.polygon([(cx-6, cy-8), (cx+6, cy-8), (cx, cy)], fill=pin_color)
    draw.ellipse([cx-5, cy-21, cx+5, cy-11], fill='#fff')

    # Scale bar simple (no precisa pero da escala)
    # A esta latitud, 1° de longitud ≈ 111 * cos(lat) km
    n = 2 ** zoom
    deg_per_tile = 360 / n
    km_per_tile = deg_per_tile * 111 * math.cos(math.radians(lat))
    px_per_km = TILE_SIZE / km_per_tile
    # Bar de ~50 km o ~20 km según escala
    bar_km = 50 if px_per_km < 4 else 20 if px_per_km < 10 else 10
    bar_px = int(bar_km * px_per_km)
    # Esquina inferior izquierda
    bx, by = 14, height - 22
    draw.rectangle([bx, by, bx+bar_px, by+4], fill='#222')
    draw.text((bx + bar_px + 6, by - 5), f"{bar_km} km", fill='#222')

    # Crédito (legalmente requerido por CARTO/OSM)
    credit = "© OpenStreetMap · CARTO"
    draw.text((width - 140, height - 15), credit, fill='#666')

    return final

def render_to_base64(lat, lng, **kwargs):
    img = render_land_map(lat, lng, **kwargs)
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return base64.b64encode(buf.getvalue()).decode('ascii')

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--lat', type=float, required=True)
    p.add_argument('--lng', type=float, required=True)
    p.add_argument('--zoom', type=int, default=10)
    p.add_argument('--out', default='/tmp/test_map.png')
    args = p.parse_args()
    img = render_land_map(args.lat, args.lng, zoom=args.zoom)
    img.save(args.out)
    print(f"Map saved → {args.out}")
