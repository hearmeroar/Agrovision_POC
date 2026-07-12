"""
Offline batch tool: precomputes crop_benchmarks.json for every crop in the
EuroCrops (Slovenia) sample, instead of app.py's normal lazy/on-demand path
(computed only the first time a field of that crop is actually viewed).

Reuses the exact same methodology as app.py's _monthly_ndvi_series /
_crop_benchmark_series (real CDSE Sentinel Hub NDVI, masked to each field's
own polygon, averaged across up to BENCHMARK_FIELDS_PER_CROP fields per crop).
Saves incrementally via crop_benchmarks.save_benchmark after each crop, so an
interruption doesn't lose already-computed crops.

Run: python3 build_crop_benchmarks.py
"""

import calendar
import datetime
import io
import os
import sys
import time

import numpy as np
import tifffile
import toml
from PIL import Image, ImageDraw

secrets_path = os.path.join(os.path.dirname(__file__), ".streamlit", "secrets.toml")
if os.path.exists(secrets_path):
    secrets = toml.load(secrets_path)
    os.environ.setdefault("CDSE_CLIENT_ID", secrets["cdse"]["client_id"])
    os.environ.setdefault("CDSE_CLIENT_SECRET", secrets["cdse"]["client_secret"])

import fetch_sat
import eurocrops_fields
import crop_benchmarks
import crop_mapping

BENCHMARK_FIELDS_PER_CROP = 10
MIN_BENCHMARK_FIELD_AREA_HA = 0.3  # below this, too few clean Sentinel-2 (10m) pixels post-masking
# Fixed to the EuroCrops declaration year (2023), not "last full year": that's
# the one year we actually know the declared crop was correct. A later year
# risks the field having rotated to a different crop since the declaration.
BENCHMARK_YEAR = 2023


def _balanced_candidate_sample(candidates, n):
    """
    Round-robins across country groups (parsed from the "EuroCrops <CC> #"
    label prefix) instead of a plain sorted-prefix slice. A plain
    sorted(candidates)[:n] would always pick "EuroCrops SI ..." labels first
    ('I' < 'K' alphabetically) whenever Slovenia alone already has >= n
    eligible fields for that crop — silently ignoring the pooled Slovak
    candidates for every common crop and defeating the point of pooling.
    """
    groups = {}
    for label in candidates:
        country = label.split()[1] if label.startswith("EuroCrops ") else "?"
        groups.setdefault(country, []).append(label)
    for group in groups.values():
        group.sort()
    picked = []
    while len(picked) < n and any(groups.values()):
        for country in sorted(groups):
            if groups[country]:
                picked.append(groups[country].pop(0))
                if len(picked) >= n:
                    break
    return picked


def _project_polygon_to_pixels(polygon_coords, bbox, size):
    lon_min, lon_max, lat_min, lat_max = bbox
    width, height = size
    return [
        (
            (lon - lon_min) / (lon_max - lon_min) * width,
            (1 - (lat - lat_min) / (lat_max - lat_min)) * height,
        )
        for lon, lat in polygon_coords
    ]


def _polygon_mask(polygon_coords, bbox, size):
    points = _project_polygon_to_pixels(polygon_coords, bbox, size)
    mask_img = Image.new("L", size, 0)
    ImageDraw.Draw(mask_img).polygon(points, fill=255)
    return np.array(mask_img) > 0


def _monthly_ndvi_series(polygon_coords, padded_bbox, year):
    today = datetime.date.today()
    series = {}
    for month in range(1, 13):
        month_start = datetime.date(year, month, 1)
        if month_start > today:
            break
        last_day = calendar.monthrange(year, month)[1]
        date_from = month_start.isoformat()
        date_to = min(datetime.date(year, month, last_day), today).isoformat()
        try:
            tiff_bytes = fetch_sat.fetch_ndvi_raster(polygon_coords, date_from, date_to)
            arr = tifffile.imread(io.BytesIO(tiff_bytes))
            ndvi_band = arr[..., 0]
            data_mask = arr[..., 3] > 0.5
            field_mask = _polygon_mask(polygon_coords, padded_bbox, (ndvi_band.shape[1], ndvi_band.shape[0]))
            valid = data_mask & field_mask
            series[month] = float(ndvi_band[valid].mean()) if valid.sum() > 0 else None
        except Exception as exc:
            print(f"    month {month}: ERROR {exc}", file=sys.stderr)
            series[month] = None
    return series


def build_benchmark(crop_name, all_fields, all_crops, all_areas):
    # Match by canonical crop, not the raw string, so e.g. Slovak "Pšenica
    # letná ozimná" and Slovenian "pšenica (ozimna)" (both winter wheat) pool
    # together instead of two disjoint, thinner benchmarks.
    target_canonical = crop_mapping.canonical_crop(crop_name)
    candidates = sorted(
        label for label, crop in all_crops.items()
        if crop_mapping.canonical_crop(crop) == target_canonical
        and all_areas.get(label, 0) >= MIN_BENCHMARK_FIELD_AREA_HA
    )
    sample_labels = _balanced_candidate_sample(candidates, BENCHMARK_FIELDS_PER_CROP)

    per_month_values = {month: [] for month in range(1, 13)}
    for label in sample_labels:
        t0 = time.time()
        polygon = all_fields[label]
        padded = fetch_sat._padded_bbox(polygon, pad_ratio=0.5)
        padded_bbox = (padded[0], padded[2], padded[1], padded[3])
        series = _monthly_ndvi_series(polygon, padded_bbox, BENCHMARK_YEAR)
        for month, value in series.items():
            if value is not None:
                per_month_values[month].append(value)
        print(f"    {label}: {time.time() - t0:.1f}s", flush=True)

    benchmark_mean = {
        month: (float(np.mean(values)) if values else None)
        for month, values in per_month_values.items()
    }
    benchmark_std = {
        month: (float(np.std(values)) if len(values) > 1 else 0.0 if values else None)
        for month, values in per_month_values.items()
    }
    return benchmark_mean, benchmark_std, sample_labels


def main():
    # SI + SK merged: crop names differ by country/language so they don't
    # collide, this just lets each country's fields build a benchmark from
    # other fields of the same country/crop.
    all_fields = {**eurocrops_fields.load_eurocrops_fields(country_code="SI"),
                  **eurocrops_fields.load_eurocrops_fields(country_code="SK")}
    all_crops = {**eurocrops_fields.load_eurocrops_field_crops(country_code="SI"),
                 **eurocrops_fields.load_eurocrops_field_crops(country_code="SK")}
    all_areas = {**eurocrops_fields.load_eurocrops_field_areas(country_code="SI"),
                 **eurocrops_fields.load_eurocrops_field_areas(country_code="SK")}

    eligible_crop_counts = {}
    for label, crop in all_crops.items():
        if all_areas.get(label, 0) >= MIN_BENCHMARK_FIELD_AREA_HA:
            eligible_crop_counts[crop] = eligible_crop_counts.get(crop, 0) + 1
    crops_by_count = sorted(eligible_crop_counts.items(), key=lambda kv: -kv[1])

    for crop_name, eligible_count in crops_by_count:
        existing_mean, existing_std, existing_fields = crop_benchmarks.load_benchmark(crop_name, BENCHMARK_YEAR)
        has_enough_fields = (
            existing_mean is not None
            and len(existing_fields) >= min(BENCHMARK_FIELDS_PER_CROP, eligible_count)
        )
        if has_enough_fields and existing_std:
            print(f"SKIP (already cached with enough fields + std): {crop_name} ({eligible_count} eligible fields)", flush=True)
            continue
        print(f"BUILDING: {crop_name} ({eligible_count} eligible fields, >= {MIN_BENCHMARK_FIELD_AREA_HA} ha) year={BENCHMARK_YEAR}", flush=True)
        t0 = time.time()
        benchmark_mean, benchmark_std, sample_labels = build_benchmark(crop_name, all_fields, all_crops, all_areas)
        crop_benchmarks.save_benchmark(crop_name, BENCHMARK_YEAR, benchmark_mean, benchmark_std, sample_labels)
        print(f"  saved {crop_name} in {time.time() - t0:.1f}s (fields used: {len(sample_labels)})", flush=True)

    print("DONE", flush=True)


if __name__ == "__main__":
    main()
