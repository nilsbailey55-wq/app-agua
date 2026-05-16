# Scripts de generación de datos — App Agua

Estos scripts regeneran los datasets geoespaciales principales de la app
a partir de fuentes públicas (OSM, HydroRIVERS).

**No es necesario correrlos para usar la app** — los archivos generados
ya están en `backend/data/`. Los scripts son documentación del pipeline
y permiten regenerar si OSM mejora o si querés extender la cobertura.

---

## Scripts disponibles

### 1. `build_lakes_from_osm.py`
**Genera:** `backend/data/ar_lakes.geojson`

**Fuente:** OpenStreetMap vía Overpass API

**Qué hace:**
- Descarga todas las relations y ways `natural=water` con nombre en Argentina
- Ensambla polígonos desde miembros outer/inner de cada relation OSM
- Filtra cuerpos de agua de Chile/Bolivia (intersección < 5% con territorio AR)
- Elimina ríos disfrazados de lagos (cross-reference con nombres HydroRIVERS)
- Agrega salares puneños (mapeados como `natural=wetland` en OSM)
- Simplifica geometrías adaptativamente por área
- Garantiza que todas las features tengan `area_km2`, `water_type`, `osm_id`

**Uso:**
```bash
cd backend/scripts
python3 build_lakes_from_osm.py
```

**Tiempo estimado:** ~15-20 minutos  
**Requisitos:** `shapely`, `curl`

**Cuándo correrlo:**
- Si OSM mejoró las geometrías de un lago
- Si querés agregar lagos nuevos que ahora tienen relation en OSM
- Si el archivo actual está corrupto

---

### 2. `build_rivers_geom.py`
**Genera:** `backend/data/ar_rivers_geom.geojson`

**Fuentes:**
- HydroRIVERS v10 (HydroSHEDS) — requiere shapefile descargado aparte
- OpenStreetMap (Overpass API) — para cabeceras sin cobertura HR

**Qué hace:**
- Lee HydroRIVERS shapefile (South America), filtra Strahler ≥ 4
- Asigna nombres desde `ar_rivers_names.json` por HYRIV_ID
- Descarga de OSM todos los `waterway=river|stream` con nombre
- Agrega los segmentos OSM que no están cubiertos por HydroRIVERS (cabeceras)
- Cierra gaps de endpoints entre segmentos adyacentes (< 100m)

**Uso:**
```bash
# Con HydroRIVERS (recomendado):
cd backend/scripts
python3 build_rivers_geom.py --hr /ruta/a/HydroRIVERS_v10_sa_shp/

# Solo OSM (sin HR):
python3 build_rivers_geom.py
```

**Tiempo estimado:** ~30-60 minutos  
**Requisitos:** `shapely`, `pyshp`, `curl`

**HydroRIVERS:**  
Descargá South America desde https://www.hydrosheds.org/products/hydrorivers  
→ Archivo: `HydroRIVERS_v10_sa.zip` (~150 MB, gratis, registro requerido)

**Cuándo correrlo:**
- Si querés agregar ríos nuevos de OSM (más headwaters)
- Si cambiás el umbral de Strahler mínimo

---

## Decisiones de diseño documentadas

### Lagos: ¿por qué OSM relations y no ways?
Las relations `type=multipolygon` en OSM contienen la topología correcta:
outer rings (costa exterior) e inner rings (islas). Los ways sueltos suelen
ser solo uno de los brazos del lago. Usar relations garantiza polígonos
completos incluso para lagos con brazos complejos (Nahuel Huapi, San Martín).

### Lagos: ¿por qué no JRC Global Surface Water?
JRC (Pekel et al. 2016, 30m Landsat) sería más preciso para cuerpos de agua
dinámicos/estacionales, pero no trae nombres asociados. El pipeline requeriría
un join geoespacial contra OSM/IGN para etiquetar. Queda en el tintero como
mejora futura, especialmente para humedales temporales pampeanos.

### Ríos: umbral Strahler ≥ 4
HydroRIVERS usa Strahler order 1-9. A Strahler 4 en Argentina corresponden
ríos con cuenca ≥ ~500 km² — visualmente significativos a escala provincial.
Los Strahler 1-3 (quebradas, arroyos) se cubren via OSM-only headwaters
en zoom alto (≥ 9).

### Ríos OSM: proxy de Strahler
Los segmentos OSM-only no tienen Strahler real. Se usa un proxy:
- `waterway=river` → s=4 (equivalente a HR mínimo)
- `waterway=stream` → s=2
- caudal estimado: `q = max(1, min(15, length_km * 0.3))`

### Filtro "ríos disfrazados de lagos"
En OSM algunos tramos anchos de ríos se mapean como `natural=water` cerrado.
El script los detecta en dos pasos:
1. Prefijos explícitos: "Río ", "Arroyo", "Riacho", "Canal " etc.
2. Cross-reference: si el nombre (sin prefijo) aparece en `ar_rivers_names.json`

### Endpoint clustering (100m)
HydroRIVERS fue derivado de SRTM (30m DEM). Los segmentos de ríos adyacentes
a veces quedan con gaps de hasta ~100m. El clustering mueve los endpoints
al promedio para que la animación de flujo (Leaflet canvas) muestre líneas
continuas sin "saltos" visibles.

---

## Archivos que NO se regeneran con scripts

Estos se mantienen/editan manualmente (son datasets curados, pequeños):

| Archivo | Descripción |
|---------|-------------|
| `ar_citizen_conflicts.geojson` | 8 conflictos socioambientales documentados |
| `ar_ramsar.geojson` | 23 sitios Ramsar de Argentina |
| `ar_indigenous.geojson` | Territorios indígenas por cuenca |
| `ar_dams.geojson` | Diques y embalses principales |
| `ar_cities.geojson` | Ciudades principales |
| `ar_aquifers.geojson` | Sistemas acuíferos |
| `basins.json` | Definición de cuencas (manual + HydroBASINS) |

---

## Notas operativas

### Reiniciar el backend después de regenerar
Los datos se cargan en memoria al iniciar uvicorn. Después de regenerar
cualquier archivo `backend/data/`, reiniciar:

```bash
pkill -f "uvicorn main:app"
cd backend
uvicorn main:app --port 8000
```

O usar `start.sh` (con `--reload`) desde la raíz del proyecto:
```bash
./start.sh
```

### Rate limiting de Overpass
Overpass API es pública y tiene rate limiting. Los scripts incluyen `sleep`
entre chunks. Si obtenés error 429, esperá 2-3 minutos y reintentá.
Overpass alternativo: https://overpass.karte.io/
