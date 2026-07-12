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


def load_eurocrops_fields(path: str = DEFAULT_SAMPLE_PATH) -> dict:
    """
    Reads the bundled EuroCrops (Slovenia) GeoJSON sample and returns
    {label: closed_polygon}, same [lon, lat] format as app.py's FIELDS dict.
    Only the exterior ring of each polygon is used.
    """
    with open(path) as f:
        data = json.load(f)

    fields = {}
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
        fields[label] = exterior_ring

    return fields
