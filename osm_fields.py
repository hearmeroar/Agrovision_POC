"""
OpenStreetMap field-boundary lookup for the eAgrar dashboard.

Not an official cadastre source: these are landuse polygons traced by volunteer
mappers, with no official parcel number (broj parcele) or owner attached. Used
as a free, keyless stand-in for real geometry until RGZ cadastral API access
(rest.geosrbija.rs/api/dkp/v1, x-access-token) is obtained.
"""

import logging

import requests

logger = logging.getLogger("eagrar.osm_fields")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
FIELD_LANDUSE_TYPES = ("farmland", "orchard", "vineyard")

# Overpass's Apache front-end returns 406 for the default python-requests User-Agent.
REQUEST_HEADERS = {"User-Agent": "AgrovisionPOC/1.0 (eagrar dashboard)"}


def fetch_osm_fields(bbox: tuple[float, float, float, float], limit: int = 15) -> dict:
    """
    bbox: (lon_min, lon_max, lat_min, lat_max).

    Queries Overpass for landuse=farmland/orchard/vineyard ways inside bbox and
    returns {label: closed_polygon_coords}, ready to merge into app.py's FIELDS
    dict (same [lon, lat] format, first point == last point).
    """
    lon_min, lon_max, lat_min, lat_max = bbox
    landuse_filter = "|".join(FIELD_LANDUSE_TYPES)
    query = (
        "[out:json][timeout:25];"
        f'way["landuse"~"^({landuse_filter})$"]'
        f"({lat_min},{lon_min},{lat_max},{lon_max});"
        "out geom;"
    )

    response = requests.post(OVERPASS_URL, data={"data": query}, headers=REQUEST_HEADERS, timeout=30)
    if response.status_code != 200:
        logger.error("Overpass query failed: %s %s", response.status_code, response.text[:300])
        raise RuntimeError(f"Failed to fetch OSM fields (HTTP {response.status_code}).")

    elements = response.json().get("elements", [])
    fields = {}
    for way in elements[:limit]:
        geometry = way.get("geometry")
        if not geometry or len(geometry) < 3:
            continue
        polygon = [[pt["lon"], pt["lat"]] for pt in geometry]
        if polygon[0] != polygon[-1]:
            polygon.append(polygon[0])

        name = way.get("tags", {}).get("name")
        label = f"OSM: {name}" if name else f"OSM field #{way['id']}"
        fields[label] = polygon

    logger.info("Fetched %d OSM field polygons for bbox %s", len(fields), bbox)
    return fields
