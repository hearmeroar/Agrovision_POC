"""
CDSE / Sentinel Hub OAuth2 integration for the eAgrar AI-Verification System.

Responsibilities:
  1. Exchange Client ID / Client Secret for an access_token (OAuth2 Client Credentials Grant, RFC 6749 §4.4).
  2. Cache the token in process memory, honoring its TTL (Keycloak defaults to 600s).
  3. Build the Bearer header and payloads for the Sentinel Hub Process API (NDVI evalscripts).

Credentials are never hardcoded: st.secrets takes priority, environment variables are the fallback.
"""

import os
import time
import logging
from dataclasses import dataclass
from typing import Optional

import requests
import streamlit as st

logger = logging.getLogger("eagrar.fetch_sat")

# ---------------------------------------------------------------------------
# IMPORTANT: the CDSE OAuth2 token endpoint is the Keycloak realm "CDSE", not "copernicus.eu".
# Source: the official Copernicus Data Space Ecosystem portal (dataspace.copernicus.eu),
# Authentication / API documentation section.
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SENTINEL_HUB_PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

TOKEN_SAFETY_MARGIN_SEC = 60  # refresh the token 60s before it actually expires


@dataclass
class TokenBundle:
    access_token: str
    expires_at: float  # unix timestamp


_token_cache: Optional[TokenBundle] = None


def _get_credentials() -> tuple[str, str]:
    """
    Credential lookup order:
      1. .streamlit/secrets.toml -> [cdse] client_id / client_secret
      2. ENV: CDSE_CLIENT_ID / CDSE_CLIENT_SECRET
    """
    try:
        return st.secrets["cdse"]["client_id"], st.secrets["cdse"]["client_secret"]
    except Exception:
        pass

    client_id = os.environ.get("CDSE_CLIENT_ID")
    client_secret = os.environ.get("CDSE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "CDSE credentials not found. Add them to .streamlit/secrets.toml "
            "([cdse] client_id=\"...\" client_secret=\"...\") or set the "
            "CDSE_CLIENT_ID / CDSE_CLIENT_SECRET environment variables."
        )
    return client_id, client_secret


def get_access_token(force_refresh: bool = False) -> str:
    """Returns a valid Bearer token, refreshing it if needed."""
    global _token_cache

    if not force_refresh and _token_cache and time.time() < _token_cache.expires_at:
        return _token_cache.access_token

    client_id, client_secret = _get_credentials()

    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=15,
    )

    if response.status_code != 200:
        logger.error("CDSE token exchange failed: %s %s", response.status_code, response.text)
        raise RuntimeError(
            f"Failed to obtain a CDSE token (HTTP {response.status_code}). "
            "Check the Client ID/Secret and the client status in the CDSE Dashboard "
            "(https://shapps.dataspace.copernicus.eu/dashboard/)."
        )

    payload = response.json()
    access_token = payload["access_token"]
    expires_in = payload.get("expires_in", 600)

    _token_cache = TokenBundle(
        access_token=access_token,
        expires_at=time.time() + expires_in - TOKEN_SAFETY_MARGIN_SEC,
    )
    logger.info("CDSE access_token obtained, valid for %ss", expires_in)
    return access_token


def build_auth_header() -> dict:
    """Ready-to-use header for CDSE/Sentinel Hub API requests."""
    return {"Authorization": f"Bearer {get_access_token()}"}


# ---------------------------------------------------------------------------
# Evalscript computing NDVI from Sentinel-2 L2A (bands B04=Red, B08=NIR)
NDVI_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: ["B04", "B08", "dataMask"],
    output: { bands: 4 }
  };
}
function evaluatePixel(sample) {
  let ndvi = (sample.B08 - sample.B04) / (sample.B08 + sample.B04);
  return [ndvi, ndvi, ndvi, sample.dataMask];
}
"""


def _padded_bbox(polygon_coords: list, pad_ratio: float) -> list:
    """
    Bounding box of polygon_coords, expanded by pad_ratio * (width/height) on each
    side, so the fetched imagery shows surrounding land instead of a tight crop.
    """
    lons = [pt[0] for pt in polygon_coords]
    lats = [pt[1] for pt in polygon_coords]
    lon_min, lon_max = min(lons), max(lons)
    lat_min, lat_max = min(lats), max(lats)
    lon_pad = (lon_max - lon_min) * pad_ratio
    lat_pad = (lat_max - lat_min) * pad_ratio
    return [lon_min - lon_pad, lat_min - lat_pad, lon_max + lon_pad, lat_max + lat_pad]


def build_ndvi_request_payload(
    polygon_coords: list, date_from: str, date_to: str, pad_ratio: float = 0.5
) -> dict:
    """
    polygon_coords: list of [lon, lat], a closed polygon (first point == last point).
    date_from / date_to: 'YYYY-MM-DD' strings.
    pad_ratio: how much surrounding land to include around the parcel (0 = tight crop
        to the parcel's bounding box, 0.5 = area doubled in each direction).

    Ready-to-POST payload for SENTINEL_HUB_PROCESS_URL, covering the parcel plus margin.
    """
    return {
        "input": {
            "bounds": {
                "bbox": _padded_bbox(polygon_coords, pad_ratio),
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"},
            },
            "data": [
                {
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {
                            "from": f"{date_from}T00:00:00Z",
                            "to": f"{date_to}T23:59:59Z",
                        },
                        "maxCloudCoverage": 20,
                    },
                }
            ],
        },
        "output": {
            "width": 256,
            "height": 256,
            "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}],
        },
        "evalscript": NDVI_EVALSCRIPT,
    }


def fetch_ndvi_raster(polygon_coords: list, date_from: str, date_to: str) -> bytes:
    """Calls the Process API and returns the raw GeoTIFF (NDVI raster)."""
    headers = build_auth_header()
    headers["Accept"] = "image/tiff"
    payload = build_ndvi_request_payload(polygon_coords, date_from, date_to)

    response = requests.post(SENTINEL_HUB_PROCESS_URL, json=payload, headers=headers, timeout=60)
    if response.status_code != 200:
        logger.error("Sentinel Hub Process API error: %s %s", response.status_code, response.text)
        raise RuntimeError(
            f"Failed to fetch the NDVI raster (HTTP {response.status_code}): {response.text[:300]}"
        )
    return response.content


# ---------------------------------------------------------------------------
# Standalone colored NDVI PNG visualization (RGBA, ready for st.image) — used
# IN ADDITION to fetch_ndvi_raster/TIFF, not as a replacement for it.
# Color ramp is the standard NDVI scale (red/brown -> yellow -> green).
# Keep NDVI_COLOR_RAMP (below, used for the legend in app.py) in sync with
# the `ramp` array embedded in this evalscript if either one changes.
NDVI_VISUAL_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: ["B04", "B08", "dataMask"],
    output: { bands: 4, sampleType: "UINT8" }
  };
}

const ramp = [
  [-1.0, [0, 0, 0]],
  [-0.2, [165, 0, 38]],
  [-0.1, [215, 48, 39]],
  [0.0, [244, 109, 67]],
  [0.1, [253, 174, 97]],
  [0.2, [254, 224, 139]],
  [0.3, [217, 239, 139]],
  [0.4, [166, 217, 106]],
  [0.5, [102, 189, 99]],
  [0.6, [26, 152, 80]],
  [1.0, [0, 104, 55]]
];

function ndviToColor(ndvi) {
  ndvi = Math.max(-1, Math.min(1, ndvi));
  for (let i = 1; i < ramp.length; i++) {
    if (ndvi <= ramp[i][0]) {
      const [v0, c0] = ramp[i - 1];
      const [v1, c1] = ramp[i];
      const t = (ndvi - v0) / (v1 - v0);
      return [
        Math.round(c0[0] + (c1[0] - c0[0]) * t),
        Math.round(c0[1] + (c1[1] - c0[1]) * t),
        Math.round(c0[2] + (c1[2] - c0[2]) * t)
      ];
    }
  }
  return ramp[ramp.length - 1][1];
}

function evaluatePixel(sample) {
  let ndvi = (sample.B08 - sample.B04) / (sample.B08 + sample.B04 + 1e-6);
  let [r, g, b] = ndviToColor(ndvi);
  return [r, g, b, sample.dataMask === 1 ? 255 : 0];
}
"""

# Python mirror of the `ramp` array above, for rendering a matching legend in the UI.
NDVI_COLOR_RAMP = [
    (-1.0, (0, 0, 0)),
    (-0.2, (165, 0, 38)),
    (-0.1, (215, 48, 39)),
    (0.0, (244, 109, 67)),
    (0.1, (253, 174, 97)),
    (0.2, (254, 224, 139)),
    (0.3, (217, 239, 139)),
    (0.4, (166, 217, 106)),
    (0.5, (102, 189, 99)),
    (0.6, (26, 152, 80)),
    (1.0, (0, 104, 55)),
]


def build_ndvi_visual_payload(polygon_coords: list, date_from: str, date_to: str) -> dict:
    """Payload for a ready-to-display NDVI PNG (8-bit per channel, for quick UI previews)."""
    payload = build_ndvi_request_payload(polygon_coords, date_from, date_to)
    payload["output"]["responses"] = [{"identifier": "default", "format": {"type": "image/png"}}]
    payload["evalscript"] = NDVI_VISUAL_EVALSCRIPT
    return payload


def fetch_ndvi_png(polygon_coords: list, date_from: str, date_to: str) -> bytes:
    """Calls the Process API and returns a ready-to-display NDVI PNG snapshot (8-bit per channel)."""
    headers = build_auth_header()
    headers["Accept"] = "image/png"
    payload = build_ndvi_visual_payload(polygon_coords, date_from, date_to)

    response = requests.post(SENTINEL_HUB_PROCESS_URL, json=payload, headers=headers, timeout=60)
    if response.status_code != 200:
        logger.error("Sentinel Hub Process API error: %s %s", response.status_code, response.text)
        raise RuntimeError(
            f"Failed to fetch the NDVI PNG snapshot (HTTP {response.status_code}): {response.text[:300]}"
        )
    return response.content


# ---------------------------------------------------------------------------
# Generic visual-product pipeline: every other index/composite below (True
# Color, NDMI, NDWI, NDRE, EVI, SAVI, SCL, Sentinel-1 VV/VH/RVI) shares one
# request builder and one fetch function, parameterized by evalscript and
# data collection. NDVI above is kept as dedicated functions since it's the
# primary product and predates this generalization.

def build_visual_payload(
    polygon_coords: list,
    date_from: str,
    date_to: str,
    data_type: str,
    evalscript: str,
    cloud_filter: bool = False,
    sar: bool = False,
    pad_ratio: float = 0.5,
) -> dict:
    """Ready-to-POST Process API payload for any single-collection visual PNG product."""
    data_filter = {
        "timeRange": {
            "from": f"{date_from}T00:00:00Z",
            "to": f"{date_to}T23:59:59Z",
        },
    }
    if cloud_filter:
        data_filter["maxCloudCoverage"] = 20

    data_entry = {"type": data_type, "dataFilter": data_filter}
    if sar:
        data_entry["processing"] = {"orthorectify": True, "backCoeff": "GAMMA0_TERRAIN"}

    return {
        "input": {
            "bounds": {
                "bbox": _padded_bbox(polygon_coords, pad_ratio),
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"},
            },
            "data": [data_entry],
        },
        "output": {
            "width": 256,
            "height": 256,
            "responses": [{"identifier": "default", "format": {"type": "image/png"}}],
        },
        "evalscript": evalscript,
    }


def fetch_visual_png(
    polygon_coords: list,
    date_from: str,
    date_to: str,
    data_type: str,
    evalscript: str,
    cloud_filter: bool = False,
    sar: bool = False,
) -> bytes:
    """Calls the Process API and returns a ready-to-display PNG for the given evalscript/collection."""
    headers = build_auth_header()
    headers["Accept"] = "image/png"
    payload = build_visual_payload(
        polygon_coords, date_from, date_to, data_type, evalscript, cloud_filter=cloud_filter, sar=sar
    )

    response = requests.post(SENTINEL_HUB_PROCESS_URL, json=payload, headers=headers, timeout=60)
    if response.status_code != 200:
        logger.error("Sentinel Hub Process API error: %s %s", response.status_code, response.text)
        raise RuntimeError(
            f"Failed to fetch the snapshot (HTTP {response.status_code}): {response.text[:300]}"
        )
    return response.content


def _ramp_index_evalscript(input_bands: list, formula_js: str, ramp: list) -> str:
    """
    Builds an evalscript for a normalized-difference-style index: computes `value`
    via formula_js (must assign a `value` variable in range [-1, 1]), then colors it
    through `ramp` ([(threshold, (r,g,b)), ...], same format as NDVI_COLOR_RAMP).
    """
    inputs_js = ", ".join(f'"{b}"' for b in input_bands)
    ramp_js_lines = ",\n  ".join(f"[{v}, [{r}, {g}, {b}]]" for v, (r, g, b) in ramp)
    return f"""
//VERSION=3
function setup() {{
  return {{
    input: [{inputs_js}, "dataMask"],
    output: {{ bands: 4, sampleType: "UINT8" }}
  }};
}}

const ramp = [
  {ramp_js_lines}
];

function valueToColor(x) {{
  x = Math.max(-1, Math.min(1, x));
  for (let i = 1; i < ramp.length; i++) {{
    if (x <= ramp[i][0]) {{
      const [v0, c0] = ramp[i - 1];
      const [v1, c1] = ramp[i];
      const t = (x - v0) / (v1 - v0);
      return [
        Math.round(c0[0] + (c1[0] - c0[0]) * t),
        Math.round(c0[1] + (c1[1] - c0[1]) * t),
        Math.round(c0[2] + (c1[2] - c0[2]) * t)
      ];
    }}
  }}
  return ramp[ramp.length - 1][1];
}}

function evaluatePixel(sample) {{
  {formula_js}
  let [r, g, b] = valueToColor(value);
  return [r, g, b, sample.dataMask === 1 ? 255 : 0];
}}
"""


# Dry-to-wet ramp used for moisture/water indices (NDMI, NDWI) — brown (dry)
# through pale neutral to blue (wet/water), same [-1, 1] domain as NDVI_COLOR_RAMP.
MOISTURE_COLOR_RAMP = [
    (-1.0, (140, 100, 60)),
    (-0.3, (191, 165, 118)),
    (0.0, (230, 230, 210)),
    (0.3, (140, 190, 230)),
    (1.0, (0, 90, 180)),
]

# ---- Sentinel-2 True Color / False Color Infrared (standard Sentinel Hub scripts) ----
TRUE_COLOR_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: ["B02", "B03", "B04", "dataMask"],
    output: { bands: 4 }
  };
}
function evaluatePixel(sample) {
  return [2.5 * sample.B04, 2.5 * sample.B03, 2.5 * sample.B02, sample.dataMask];
}
"""

FALSE_COLOR_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: ["B03", "B04", "B08", "dataMask"],
    output: { bands: 4 }
  };
}
function evaluatePixel(sample) {
  return [2.5 * sample.B08, 2.5 * sample.B04, 2.5 * sample.B03, sample.dataMask];
}
"""

# ---- Normalized-difference indices, colored via the shared ramp helper ----
NDMI_EVALSCRIPT = _ramp_index_evalscript(
    ["B08", "B11"],
    "let value = (sample.B08 - sample.B11) / (sample.B08 + sample.B11 + 1e-6);",
    MOISTURE_COLOR_RAMP,
)

NDWI_EVALSCRIPT = _ramp_index_evalscript(
    ["B03", "B08"],
    "let value = (sample.B03 - sample.B08) / (sample.B03 + sample.B08 + 1e-6);",
    MOISTURE_COLOR_RAMP,
)

NDRE_EVALSCRIPT = _ramp_index_evalscript(
    ["B08", "B05"],
    "let value = (sample.B08 - sample.B05) / (sample.B08 + sample.B05 + 1e-6);",
    NDVI_COLOR_RAMP,
)

EVI_EVALSCRIPT = _ramp_index_evalscript(
    ["B08", "B04", "B02"],
    "let value = 2.5 * (sample.B08 - sample.B04) / "
    "(sample.B08 + 6 * sample.B04 - 7.5 * sample.B02 + 1 + 1e-6);",
    NDVI_COLOR_RAMP,
)

SAVI_EVALSCRIPT = _ramp_index_evalscript(
    ["B08", "B04"],
    "let value = (sample.B08 - sample.B04) / (sample.B08 + sample.B04 + 0.5 + 1e-6) * 1.5;",
    NDVI_COLOR_RAMP,
)

# ---- Scene Classification Layer (categorical, standard ESA/SCL palette) ----
SCL_LEGEND = [
    (0, "No data", (0, 0, 0)),
    (1, "Saturated/defective", (255, 0, 0)),
    (2, "Dark area", (47, 47, 47)),
    (3, "Cloud shadow", (100, 50, 0)),
    (4, "Vegetation", (0, 160, 0)),
    (5, "Bare soil", (255, 230, 90)),
    (6, "Water", (0, 0, 255)),
    (7, "Cloud (low prob.)", (128, 128, 128)),
    (8, "Cloud (medium prob.)", (192, 192, 192)),
    (9, "Cloud (high prob.)", (255, 255, 255)),
    (10, "Thin cirrus", (100, 200, 255)),
    (11, "Snow/ice", (255, 150, 255)),
]

SCL_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: ["SCL", "dataMask"],
    output: { bands: 4, sampleType: "UINT8" }
  };
}
const SCL_COLORS = {
  0: [0, 0, 0],
  1: [255, 0, 0],
  2: [47, 47, 47],
  3: [100, 50, 0],
  4: [0, 160, 0],
  5: [255, 230, 90],
  6: [0, 0, 255],
  7: [128, 128, 128],
  8: [192, 192, 192],
  9: [255, 255, 255],
  10: [100, 200, 255],
  11: [255, 150, 255]
};
function evaluatePixel(sample) {
  const c = SCL_COLORS[sample.SCL] || [0, 0, 0];
  return [c[0], c[1], c[2], sample.dataMask === 1 ? 255 : 0];
}
"""

# ---- Sentinel-1 GRD: VV/VH grayscale backscatter + RVI (radar vegetation index) ----
VV_GRAYSCALE_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: ["VV", "dataMask"],
    output: { bands: 4, sampleType: "UINT8" }
  };
}
function evaluatePixel(sample) {
  let db = 10 * Math.log10(sample.VV + 1e-10);
  let scaled = Math.max(0, Math.min(255, Math.round((db + 25) / 25 * 255)));
  return [scaled, scaled, scaled, sample.dataMask === 1 ? 255 : 0];
}
"""

VH_GRAYSCALE_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: ["VH", "dataMask"],
    output: { bands: 4, sampleType: "UINT8" }
  };
}
function evaluatePixel(sample) {
  let db = 10 * Math.log10(sample.VH + 1e-10);
  let scaled = Math.max(0, Math.min(255, Math.round((db + 25) / 25 * 255)));
  return [scaled, scaled, scaled, sample.dataMask === 1 ? 255 : 0];
}
"""

# RVI (dual-pol radar vegetation index, Trudel et al.) = 4*VH/(VV+VH), range [0, 2].
# Shifted to value-1 so it lands in the same [-1, 1] domain as NDVI_COLOR_RAMP.
RVI_EVALSCRIPT = _ramp_index_evalscript(
    ["VV", "VH"],
    "let value = (4 * sample.VH / (sample.VV + sample.VH + 1e-6)) - 1;",
    NDVI_COLOR_RAMP,
)

# ---------------------------------------------------------------------------
# Registry driving the "show everything" product grid in app.py. Each entry:
#   key, label, data_type, evalscript, cloud_filter (S2 only), sar (S1 processing),
#   legend ("none" | "ramp" with ramp+ramp_ticks | "categorical" using SCL_LEGEND).
PRODUCTS = [
    {
        "key": "ndvi", "label": "NDVI (vegetation)", "data_type": "sentinel-2-l2a",
        "evalscript": NDVI_VISUAL_EVALSCRIPT, "cloud_filter": True, "sar": False,
        "legend": "ramp", "ramp": NDVI_COLOR_RAMP, "ramp_ticks": [-1, -0.5, 0, 0.3, 0.6, 1],
    },
    {
        "key": "true_color", "label": "True Color (RGB)", "data_type": "sentinel-2-l2a",
        "evalscript": TRUE_COLOR_EVALSCRIPT, "cloud_filter": True, "sar": False, "legend": "none",
    },
    {
        "key": "false_color", "label": "False Color Infrared", "data_type": "sentinel-2-l2a",
        "evalscript": FALSE_COLOR_EVALSCRIPT, "cloud_filter": True, "sar": False, "legend": "none",
    },
    {
        "key": "ndmi", "label": "NDMI (moisture)", "data_type": "sentinel-2-l2a",
        "evalscript": NDMI_EVALSCRIPT, "cloud_filter": True, "sar": False,
        "legend": "ramp", "ramp": MOISTURE_COLOR_RAMP, "ramp_ticks": [-1, -0.3, 0, 0.3, 1],
    },
    {
        "key": "ndwi", "label": "NDWI (water)", "data_type": "sentinel-2-l2a",
        "evalscript": NDWI_EVALSCRIPT, "cloud_filter": True, "sar": False,
        "legend": "ramp", "ramp": MOISTURE_COLOR_RAMP, "ramp_ticks": [-1, -0.3, 0, 0.3, 1],
    },
    {
        "key": "ndre", "label": "NDRE (red edge)", "data_type": "sentinel-2-l2a",
        "evalscript": NDRE_EVALSCRIPT, "cloud_filter": True, "sar": False,
        "legend": "ramp", "ramp": NDVI_COLOR_RAMP, "ramp_ticks": [-1, -0.5, 0, 0.3, 0.6, 1],
    },
    {
        "key": "evi", "label": "EVI", "data_type": "sentinel-2-l2a",
        "evalscript": EVI_EVALSCRIPT, "cloud_filter": True, "sar": False,
        "legend": "ramp", "ramp": NDVI_COLOR_RAMP, "ramp_ticks": [-1, -0.5, 0, 0.3, 0.6, 1],
    },
    {
        "key": "savi", "label": "SAVI", "data_type": "sentinel-2-l2a",
        "evalscript": SAVI_EVALSCRIPT, "cloud_filter": True, "sar": False,
        "legend": "ramp", "ramp": NDVI_COLOR_RAMP, "ramp_ticks": [-1, -0.5, 0, 0.3, 0.6, 1],
    },
    {
        "key": "scl", "label": "Scene Classification (SCL)", "data_type": "sentinel-2-l2a",
        "evalscript": SCL_EVALSCRIPT, "cloud_filter": True, "sar": False, "legend": "categorical",
    },
    {
        "key": "vv", "label": "Sentinel-1 VV", "data_type": "sentinel-1-grd",
        "evalscript": VV_GRAYSCALE_EVALSCRIPT, "cloud_filter": False, "sar": True, "legend": "none",
    },
    {
        "key": "vh", "label": "Sentinel-1 VH", "data_type": "sentinel-1-grd",
        "evalscript": VH_GRAYSCALE_EVALSCRIPT, "cloud_filter": False, "sar": True, "legend": "none",
    },
    {
        "key": "rvi", "label": "Sentinel-1 RVI (radar vegetation)", "data_type": "sentinel-1-grd",
        "evalscript": RVI_EVALSCRIPT, "cloud_filter": False, "sar": True,
        "legend": "ramp", "ramp": NDVI_COLOR_RAMP, "ramp_ticks": [-1, -0.5, 0, 0.3, 0.6, 1],
    },
]
