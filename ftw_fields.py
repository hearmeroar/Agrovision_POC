"""
Fields of the World (FTW) field-boundary sample for the eAgrar dashboard.

Loads sample_fields_ftw_kac.geojson: polygons predicted by Taylor Geospatial's
PRUE model on Sentinel-2 composites (see ftw_fields_N45E019_2024.README.md for
the full tile this sample was clipped from). Model output, not an official
cadastre or a human-digitized source.
"""

import json
import os

DEFAULT_SAMPLE_PATH = os.path.join(os.path.dirname(__file__), "sample_fields_ftw_kac.geojson")


def load_ftw_fields(path: str = DEFAULT_SAMPLE_PATH) -> dict:
    """
    Reads the bundled FTW GeoJSON sample and returns {label: closed_polygon},
    same [lon, lat] format as app.py's FIELDS dict. Only the exterior ring of
    each polygon is used; holes/extra rings from raster polygonization are
    dropped.
    """
    with open(path) as f:
        data = json.load(f)

    fields = {}
    for i, feature in enumerate(data["features"]):
        geometry = feature["geometry"]
        if geometry["type"] != "Polygon":
            continue
        exterior_ring = geometry["coordinates"][0]
        confidence = feature["properties"].get("confidence_mean")
        label = f"FTW field #{i} (conf {confidence:.2f})" if confidence is not None else f"FTW field #{i}"
        fields[label] = exterior_ring

    return fields
