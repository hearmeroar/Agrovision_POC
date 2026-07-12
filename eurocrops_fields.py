"""
EuroCrops field-boundary sample for the eAgrar dashboard.

Loads sample_fields_eurocrops_si.geojson: official agricultural parcel
boundaries for a patch of Prekmurje, Slovenia (flat Pannonian farmland, same
landscape type as Vojvodina), clipped from the EuroCrops harmonized dataset
(https://github.com/maja601/EuroCrops), which itself re-publishes each EU
member state's official LPIS/IACS subsidy-parcel registry — the same kind of
legal register the RGZ cadastral API would provide for Serbia, just for a
country where it's already open. Source file: si_2023.parquet from the JRC
EuroCropsV2 open-data mirror (jeodpp.jrc.ec.europa.eu), reprojected from
EPSG:3035 to WGS84.
"""

import json
import os

DEFAULT_SAMPLE_PATH = os.path.join(os.path.dirname(__file__), "sample_fields_eurocrops_si.geojson")


def _load_features(path):
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
        label = f"EuroCrops SI #{i}: {crop} ({area_ha:.2f} ha)" if area_ha is not None else f"EuroCrops SI #{i}: {crop}"
        items.append((label, exterior_ring, crop, area_ha))

    return items


def load_eurocrops_fields(path: str = DEFAULT_SAMPLE_PATH) -> dict:
    """
    Reads the bundled EuroCrops (Slovenia) GeoJSON sample and returns
    {label: closed_polygon}, same [lon, lat] format as app.py's FIELDS dict.
    """
    return {label: ring for label, ring, _crop, _area in _load_features(path)}


def load_eurocrops_field_crops(path: str = DEFAULT_SAMPLE_PATH) -> dict:
    """
    Returns {label: crop_name} for the same fields load_eurocrops_fields()
    loads — lets app.py know which crop is growing on a given field, e.g. to
    label a per-field NDVI chart.
    """
    return {label: crop for label, _ring, crop, _area in _load_features(path)}


def load_eurocrops_field_areas(path: str = DEFAULT_SAMPLE_PATH) -> dict:
    """
    Returns {label: area_ha}. Used to exclude tiny parcels from crop
    benchmarks — at Sentinel-2's 10 m pixels (0.01 ha/px), a sub-0.3 ha field
    has too few clean pixels post-masking for a reliable mean NDVI.
    """
    return {label: area for label, _ring, _crop, area in _load_features(path) if area is not None}
