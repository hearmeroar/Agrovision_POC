# ftw_fields_N45E019_2024.parquet / .geojson

Vector field boundaries for 2024, tile `N45E019` (45–46°N, 19–20°E — covers
Kać/Novi Sad and the surrounding area) from the
[Fields of the World](https://fieldsofthe.world/) (FTW) dataset. Same data,
two files: `.parquet` (33 MB, GeoParquet) and `.geojson` (~280 MB, plain
text). **Prefer the `.parquet` one** for anything programmatic — the
GeoJSON is only here for tools that can't read GeoParquet yet. Don't open
the `.geojson` in a text editor/IDE tab; at that size it will likely hang
it. Load it with geopandas, QGIS, or ogr2ogr instead.

**What this is:** not human-digitized data and not an official cadastre —
these are predictions from the PRUE model (Taylor Geospatial), trained to
segment fields from median Sentinel-2 composites. Each row is one polygon
with a `label` (`field` / `field_boundaries` / `non_field_background`) and a
model confidence score (`confidence_mean/median/min`, 0–1).

**Source:** public S3 bucket `us-west-2.opendata.source.coop`
(AWS Open Data, anonymous access, no keys/signup required), path
`ftw/global-field-boundaries/download-tiles/geoparquet/2024/N45E019.parquet`.

**Format:** GeoParquet — open it with:
```python
import geopandas as gpd
gdf = gpd.read_parquet("ftw_fields_N45E019_2024.parquet")
```
or directly in QGIS (GeoParquet support from 3.28+), or via DuckDB with the
spatial extension. The `.geojson` sibling file opens the same way via
`gpd.read_file(...)` or any GIS tool, just slower and heavier due to format.

**Caveat:** these are model predictions, not a land cadastre — no parcel
number/owner, and in this tile the average model confidence for fields near
Kać is only ~0.52 (the classification threshold is 0.5), i.e. some
predictions are borderline rather than confident. For a quality comparison,
see [sample_fields_kac.geojson](sample_fields_kac.geojson) (OpenStreetMap
field outlines) and [sample_fields_ftw_kac.geojson](sample_fields_ftw_kac.geojson)
(the same FTW model, already clipped to a small bbox around Kać).
