"""
EuroCrops field-boundary samples for the eAgrar dashboard.

Loads sample_fields_eurocrops_<country>.geojson: official agricultural
parcel boundaries clipped from the EuroCrops harmonized dataset
(https://github.com/maja601/EuroCrops), which itself re-publishes each EU
member state's official LPIS/IACS subsidy-parcel registry — the same kind of
legal register the RGZ cadastral API would provide for Serbia, just for
countries where it's already open. Source: <cc>_2023.parquet from the JRC
EuroCropsV2 open-data mirror (jeodpp.jrc.ec.europa.eu), reprojected from
EPSG:3035 to WGS84.

Two samples are bundled, both flat Pannonian-basin farmland (same landscape
as Vojvodina) with a similar crop mix (wheat/corn/barley/sunflower/rapeseed):
  - SI (sample_fields_eurocrops_si.geojson): Prekmurje, Slovenia.
  - SK (sample_fields_eurocrops_sk.geojson): Danubian Lowland near Dunajská
    Streda, Slovakia — literally the same Pannonian basin as Vojvodina, on
    its other side.
"""

import json
import os

_DIR = os.path.dirname(__file__)
SAMPLES = {
    "SI": os.path.join(_DIR, "sample_fields_eurocrops_si.geojson"),
    "SK": os.path.join(_DIR, "sample_fields_eurocrops_sk.geojson"),
}
DEFAULT_SAMPLE_PATH = SAMPLES["SI"]


def _load_features(path, country_code):
    """Shared parse: yields (label, exterior_ring, crop_name, area_ha) per feature."""
    with open(path) as f:
        data = json.load(f)

    items = []
    for i, feature in enumerate(data["features"]):
        geometry = feature["geometry"]
        if geometry["type"] == "Polygon":
            exterior_ring = geometry["coordinates"][0]
        elif geometry["type"] == "MultiPolygon":
            # Take the largest part's exterior ring; official cadastral exports
            # often wrap even single-part parcels in a MultiPolygon.
            exterior_ring = max(geometry["coordinates"], key=lambda poly: len(poly[0]))[0]
        else:
            continue
        props = feature["properties"]
        crop = props.get("crop", "unknown crop")
        area_ha = props.get("area_ha")
        label = (
            f"EuroCrops {country_code} #{i}: {crop} ({area_ha:.2f} ha)"
            if area_ha is not None
            else f"EuroCrops {country_code} #{i}: {crop}"
        )
        items.append((label, exterior_ring, crop, area_ha))

    return items


def _resolve(path, country_code):
    if path is not None:
        return path, country_code or "SI"
    country_code = country_code or "SI"
    return SAMPLES[country_code], country_code


def load_eurocrops_fields(path: str = None, country_code: str = None) -> dict:
    """
    Reads a bundled EuroCrops GeoJSON sample and returns {label: closed_polygon},
    same [lon, lat] format as app.py's FIELDS dict. Defaults to the Slovenia
    sample; pass country_code="SK" for Slovakia (or path=... for either
    explicitly).
    """
    path, country_code = _resolve(path, country_code)
    return {label: ring for label, ring, _crop, _area in _load_features(path, country_code)}


def load_eurocrops_field_crops(path: str = None, country_code: str = None) -> dict:
    """
    Returns {label: crop_name} for the same fields load_eurocrops_fields()
    loads — lets app.py know which crop is growing on a given field, e.g. to
    label a per-field NDVI chart.
    """
    path, country_code = _resolve(path, country_code)
    return {label: crop for label, _ring, crop, _area in _load_features(path, country_code)}


def load_eurocrops_field_areas(path: str = None, country_code: str = None) -> dict:
    """
    Returns {label: area_ha}. Used to exclude tiny parcels from crop
    benchmarks — at Sentinel-2's 10 m pixels (0.01 ha/px), a sub-0.3 ha field
    has too few clean pixels post-masking for a reliable mean NDVI.
    """
    path, country_code = _resolve(path, country_code)
    return {label: area for label, _ring, _crop, area in _load_features(path, country_code) if area is not None}
