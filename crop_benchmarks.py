"""
Disk-backed cache for crop NDVI benchmarks (see app.py's _crop_benchmark_series).

Methodology (also stored per-entry below, so it travels with the data):

1. Year: fixed to 2023, the EuroCrops sample's actual declaration year
   (sample_fields_eurocrops_si.geojson) — the one year we actually know the
   declared crop was correct. Using a later year (e.g. "last full year")
   risks the field having rotated to a different crop since the declaration,
   which would contaminate the benchmark with the wrong crop's NDVI curve.
2. Field pool: up to BENCHMARK_FIELDS_PER_CROP (app.py, currently 10) *other*
   EuroCrops-labeled fields declared as the same crop, excluding whichever
   field is being evaluated. Fields smaller than MIN_BENCHMARK_FIELD_AREA_HA
   (app.py, currently 0.3 ha) are excluded — at Sentinel-2's 10 m pixels
   (0.01 ha/px), a smaller field has too few clean pixels post-masking for a
   reliable mean.
3. Per field: real per-pixel NDVI (CDSE Sentinel Hub Process API, Sentinel-2
   L2A, NDVI_EVALSCRIPT with sampleType FLOAT32), masked to BOTH cloud/no-data
   pixels (dataMask) AND the field's own polygon (not just its padded
   bounding box, so neighboring fields don't leak in) — see app.py's
   _monthly_ndvi_series. Averaged into one mean NDVI per month per field.
4. Across fields: mean and standard deviation per month across the field
   pool. The mean is the benchmark line; the std draws a +/-1 std "acceptable
   range" band around it on the chart.

This file just persists that result to disk so a Streamlit reboot doesn't
have to re-spend Sentinel Hub API quota recomputing the same benchmark;
app.py checks here before calling Sentinel Hub. build_crop_benchmarks.py is
the offline batch tool that precomputes every crop's benchmark up front
instead of app.py's normal lazy/on-demand path.
"""

import json
import os

STORE_PATH = os.path.join(os.path.dirname(__file__), "crop_benchmarks.json")

METHODOLOGY = (
    "Benchmark year is fixed to 2023 (the EuroCrops sample's declaration year — the one "
    "year the declared crop is actually known to be correct; a later year risks the field "
    "having rotated to a different crop since declaration). For each crop, up to "
    "BENCHMARK_FIELDS_PER_CROP other EuroCrops-declared fields of that crop are used, "
    "excluding fields smaller than MIN_BENCHMARK_FIELD_AREA_HA (too few clean 10m Sentinel-2 "
    "pixels post-masking to trust) and excluding whichever field the app was evaluating at "
    "compute time. Per field: real NDVI (CDSE Sentinel Hub Process API, Sentinel-2 L2A, "
    "NDVI_EVALSCRIPT with sampleType FLOAT32) masked to both cloud/no-data pixels (dataMask) "
    "and the field's own polygon (not just its padded bounding box). Mean and standard "
    "deviation are then taken per month across the field pool; the std drives the +/-1 std "
    "'acceptable range' band drawn around the benchmark line."
)


def _load_store() -> dict:
    if not os.path.exists(STORE_PATH):
        return {}
    with open(STORE_PATH) as f:
        return json.load(f)


def _save_store(store: dict) -> None:
    with open(STORE_PATH, "w") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def load_benchmark(crop_name: str, year: int):
    """
    Returns (benchmark {month: mean_ndvi}, benchmark_std {month: std_ndvi},
    source_field_labels) or (None, None, None) if not cached.
    """
    store = _load_store()
    entry = store.get(crop_name, {}).get(str(year))
    if entry is None:
        return None, None, None
    benchmark = {int(month): value for month, value in entry["monthly_ndvi"].items()}
    benchmark_std = {int(month): value for month, value in entry.get("monthly_ndvi_std", {}).items()}
    return benchmark, benchmark_std, entry["source_fields"]


def save_benchmark(crop_name: str, year: int, benchmark: dict, benchmark_std: dict, source_fields: list) -> None:
    store = _load_store()
    store.setdefault(crop_name, {})[str(year)] = {
        "source_fields": source_fields,
        "monthly_ndvi": {str(month): value for month, value in benchmark.items()},
        "monthly_ndvi_std": {str(month): value for month, value in benchmark_std.items()},
        "methodology": METHODOLOGY,
    }
    _save_store(store)
