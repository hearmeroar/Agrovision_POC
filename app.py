import calendar
import datetime
import io

import streamlit as st
import numpy as np
import matplotlib.pyplot as plt
import tifffile
from PIL import Image, ImageDraw

import crop_mapping

try:
    import fetch_sat
    CDSE_MODULE_AVAILABLE = True
except ImportError:
    CDSE_MODULE_AVAILABLE = False

try:
    import osm_fields
    OSM_MODULE_AVAILABLE = True
except ImportError:
    OSM_MODULE_AVAILABLE = False

try:
    import ftw_fields
    FTW_MODULE_AVAILABLE = True
except ImportError:
    FTW_MODULE_AVAILABLE = False

try:
    import eurocrops_fields
    EUROCROPS_MODULE_AVAILABLE = True
except ImportError:
    EUROCROPS_MODULE_AVAILABLE = False

try:
    import crop_benchmarks
    CROP_BENCHMARKS_STORE_AVAILABLE = True
except ImportError:
    CROP_BENCHMARKS_STORE_AVAILABLE = False


@st.cache_data(ttl=3600, show_spinner="Loading field boundaries from OpenStreetMap...")
def _cached_osm_fields(bbox):
    return osm_fields.fetch_osm_fields(bbox)


@st.cache_data(show_spinner="Loading field boundaries from Fields of the World...")
def _cached_ftw_fields():
    return ftw_fields.load_ftw_fields()


@st.cache_data(show_spinner="Loading official EuroCrops (Slovenia) field boundaries...")
def _cached_eurocrops_fields():
    return eurocrops_fields.load_eurocrops_fields(country_code="SI")


@st.cache_data(show_spinner="Loading EuroCrops crop types...")
def _cached_eurocrops_crops():
    return eurocrops_fields.load_eurocrops_field_crops(country_code="SI")


@st.cache_data(show_spinner="Loading official EuroCrops (Slovakia) field boundaries...")
def _cached_eurocrops_fields_sk():
    return eurocrops_fields.load_eurocrops_fields(country_code="SK")


@st.cache_data(show_spinner="Loading EuroCrops crop types...")
def _cached_eurocrops_crops_sk():
    return eurocrops_fields.load_eurocrops_field_crops(country_code="SK")


@st.cache_data
def _all_eurocrops_fields_and_crops():
    """Merged SI+SK pools used by the crop-benchmark engine to find candidate
    fields — crop names differ by country/language so they naturally don't
    collide, this just lets a Slovak field's benchmark be built from other
    Slovak fields (and a Slovenian field's from other Slovenian fields)."""
    fields = {**eurocrops_fields.load_eurocrops_fields(country_code="SI"),
              **eurocrops_fields.load_eurocrops_fields(country_code="SK")}
    crops = {**eurocrops_fields.load_eurocrops_field_crops(country_code="SI"),
             **eurocrops_fields.load_eurocrops_field_crops(country_code="SK")}
    return fields, crops


def _project_polygon_to_pixels(polygon_coords, bbox, size):
    """Maps [lon, lat] polygon points onto pixel coords of a north-up image
    covering `bbox` (lon_min, lon_max, lat_min, lat_max) at `size` (w, h)."""
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
    """Boolean pixel mask (True = inside the field polygon) for `size` (w, h)."""
    points = _project_polygon_to_pixels(polygon_coords, bbox, size)
    mask_img = Image.new("L", size, 0)
    ImageDraw.Draw(mask_img).polygon(points, fill=255)
    return np.array(mask_img) > 0


@st.cache_data(show_spinner="Computing monthly NDVI from Sentinel-2 (masked to field boundary)...")
def _monthly_ndvi_series(polygon_coords, padded_bbox, year):
    """
    Mean NDVI per month for `year`, real Sentinel-2 data via the CDSE Process
    API, masked to dataMask (cloud/no-data) AND the field's own polygon (not
    just its padded bounding box) so neighboring fields don't dilute the
    value. {month: mean_ndvi or None if no cloud-free field pixels}.
    """
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
        except Exception:
            series[month] = None
    return series


BENCHMARK_FIELDS_PER_CROP = 10
MIN_BENCHMARK_FIELD_AREA_HA = 0.3  # below this, too few clean Sentinel-2 (10m) pixels post-masking
CROP_DECLARATION_YEAR = 2023  # the EuroCrops sample's actual declaration year (sample_fields_eurocrops_si.geojson)


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


@st.cache_data(show_spinner="Building crop benchmark from other fields' real Sentinel-2 history...")
def _crop_benchmark_series(crop_name, benchmark_year, exclude_label=None):
    """
    Reference NDVI-by-month curve for `crop_name`: averages the real, per-field
    -masked NDVI of up to BENCHMARK_FIELDS_PER_CROP *other* EuroCrops fields
    declared as that same crop (excluding fields smaller than
    MIN_BENCHMARK_FIELD_AREA_HA — too few pixels to trust), for `benchmark_year`.
    This is the "how this crop should behave" baseline — built from actual
    satellite history of fields with a known, official crop declaration, not
    a textbook curve. Also returns the per-month standard deviation across
    those fields, used to draw a +/-1 std "acceptable range" band around the
    benchmark line.
    Returns (mean {month: ndvi_or_None}, std {month: ndvi_or_None}, [field labels used]).
    """
    if CROP_BENCHMARKS_STORE_AVAILABLE:
        cached_mean, cached_std, cached_fields = crop_benchmarks.load_benchmark(crop_name, benchmark_year)
        if cached_mean is not None and exclude_label not in cached_fields:
            return cached_mean, cached_std, cached_fields

    all_fields, all_crops = _all_eurocrops_fields_and_crops()
    all_areas = {**eurocrops_fields.load_eurocrops_field_areas(country_code="SI"),
                 **eurocrops_fields.load_eurocrops_field_areas(country_code="SK")}
    # Match by canonical crop, not the raw string, so e.g. Slovak "Pšenica
    # letná ozimná" and Slovenian "pšenica (ozimna)" (both winter wheat)
    # pool together instead of two disjoint, thinner benchmarks.
    target_canonical = crop_mapping.canonical_crop(crop_name)
    candidates = sorted(
        label for label, crop in all_crops.items()
        if crop_mapping.canonical_crop(crop) == target_canonical and label != exclude_label
        and all_areas.get(label, 0) >= MIN_BENCHMARK_FIELD_AREA_HA
    )
    sample_labels = _balanced_candidate_sample(candidates, BENCHMARK_FIELDS_PER_CROP)

    per_month_values = {month: [] for month in range(1, 13)}
    for label in sample_labels:
        polygon = all_fields[label]
        padded = fetch_sat._padded_bbox(polygon, pad_ratio=0.5)
        padded_bbox = (padded[0], padded[2], padded[1], padded[3])
        for month, value in _monthly_ndvi_series(polygon, padded_bbox, benchmark_year).items():
            if value is not None:
                per_month_values[month].append(value)

    benchmark_mean = {
        month: (float(np.mean(values)) if values else None)
        for month, values in per_month_values.items()
    }
    benchmark_std = {
        month: (float(np.std(values)) if len(values) > 1 else 0.0 if values else None)
        for month, values in per_month_values.items()
    }

    if CROP_BENCHMARKS_STORE_AVAILABLE:
        crop_benchmarks.save_benchmark(crop_name, benchmark_year, benchmark_mean, benchmark_std, sample_labels)

    return benchmark_mean, benchmark_std, sample_labels


def _curve_correlation(actual, benchmark):
    """Pearson correlation between two {month: value} series over the months
    both have data for. None if fewer than 3 overlapping months or either
    curve is flat (zero variance, correlation undefined)."""
    common_months = [m for m in range(1, 13) if actual.get(m) is not None and benchmark.get(m) is not None]
    if len(common_months) < 3:
        return None, common_months
    a = np.array([actual[m] for m in common_months])
    b = np.array([benchmark[m] for m in common_months])
    if np.std(a) == 0 or np.std(b) == 0:
        return None, common_months
    return float(np.corrcoef(a, b)[0, 1]), common_months


def _crop_match_score(actual, benchmark, benchmark_std, correlation, common_months):
    """
    Heuristic 0-100% "match score" for the crop-declaration verdict — NOT a
    calibrated statistical probability (we have no labeled examples of a
    wrong declaration to calibrate against), just a transparent blend of:
      - shape similarity: correlation rescaled from [-1,1] to [0,1]
      - band coverage: fraction of overlapping months where the actual value
        falls within the benchmark's own +/-1 std range
    Returns (score_0_to_100 or None, band_coverage_fraction or None).
    """
    if correlation is None or not common_months:
        return None, None
    within_band = 0
    for m in common_months:
        std = benchmark_std.get(m) or 0.0
        if abs(actual[m] - benchmark[m]) <= std:
            within_band += 1
    band_coverage = within_band / len(common_months)
    corr_component = (correlation + 1) / 2
    score = 100 * (0.5 * corr_component + 0.5 * band_coverage)
    return score, band_coverage


def _best_alternative_crop(monthly_ndvi, declared_crop, year):
    """
    When a field doesn't match its declared crop, checks its curve against
    every *other* crop that already has a cached benchmark for `year` (no
    new Sentinel Hub calls — reuses whatever build_crop_benchmarks.py /
    on-demand computation already produced) and returns whichever one fits
    best, as a "might actually be X" suggestion. Returns (crop_name, score)
    or (None, None) if nothing scores.
    """
    if not CROP_BENCHMARKS_STORE_AVAILABLE:
        return None, None
    declared_canonical = crop_mapping.canonical_crop(declared_crop)
    best_crop, best_score = None, None
    for candidate_crop in crop_benchmarks.list_crops(year):
        if candidate_crop == declared_crop:
            continue
        # Skip the same real-world crop under its other-language name (e.g.
        # Slovak vs Slovenian wheat) — that's not a misdeclaration signal.
        if crop_mapping.canonical_crop(candidate_crop) == declared_canonical:
            continue
        candidate_benchmark, candidate_std, _fields = crop_benchmarks.load_benchmark(candidate_crop, year)
        if candidate_benchmark is None:
            continue
        corr, common = _curve_correlation(monthly_ndvi, candidate_benchmark)
        score, _coverage = _crop_match_score(monthly_ndvi, candidate_benchmark, candidate_std, corr, common)
        if score is not None and (best_score is None or score > best_score):
            best_crop, best_score = candidate_crop, score
    return best_crop, best_score


try:
    from streamlit_mic_recorder import speech_to_text
    VOICE_INPUT_AVAILABLE = True
except ImportError:
    VOICE_INPUT_AVAILABLE = False

# Page configuration (wide layout for split view)
st.set_page_config(layout="wide", page_title="eAgrar AI Dashboard")

# Force left text alignment everywhere (overrides Streamlit's default centered
# image captions and any inherited center/justify alignment).
# Also: the live-NDVI box gets a positioned ancestor so its loading spinner can
# be pinned absolutely on top of the (stale) snapshot instead of pushing it down.
st.markdown(
    """
    <style>
    .block-container, .block-container * {
        text-align: left !important;
    }
    div[class*="st-key-live_prod_"] {
        position: relative;
    }
    div[class*="st-key-live_prod_"] [data-testid="stSpinner"] {
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        z-index: 10;
        background: rgba(0, 0, 0, 0.75);
        padding: 6px 10px;
        border-radius: 4px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Unified clean horizontal navbar header. Reserved here (top of the page) but
# filled in below once a field is actually selected, so the subtitle reflects
# the real selected field/coordinates instead of a hardcoded claim.
def _render_header(subtitle):
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; justify-content: space-between; background-color: #1a1a1a; padding: 10px 20px; border-radius: 8px; margin-bottom: 20px;">
            <div style="display: flex; align-items: center; gap: 15px;">
                <span style="font-size: 28px;">🛰️</span>
                <span style="font-size: 22px; font-weight: bold; color: white; letter-spacing: 0.5px;">eAgrar AI-Verification System</span>
            </div>
            <div style="font-size: 14px; color: #888888; font-weight: 500;">
                {subtitle}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


header_placeholder = st.empty()
with header_placeholder:
    _render_header("Select a field below to begin live satellite monitoring")

# === LIVE CDSE CONNECTION DIAGNOSTICS (isolated, does not affect mock NDVI pipeline below) ===
if not CDSE_MODULE_AVAILABLE:
    st.warning("`fetch_sat.py` module not found or `requests` is not installed.")
else:
    with st.expander("🛰️ CDSE connection diagnostics", expanded=False):
        if st.button("Test CDSE Token Exchange"):
            try:
                fetch_sat.get_access_token(force_refresh=True)
                st.success("✅ Access token obtained. CDSE credentials are valid.")
            except Exception as exc:
                st.error(f"❌ {exc}")

    controls_col, tiles_col = st.columns([2, 3])

    with controls_col:
        FIELDS = {
            # "Parcel #4112 (Kać)": [
            #     [19.9248, 45.2882], [19.9348, 45.2885], [19.9352, 45.2842],
            #     [19.9356, 45.2808], [19.9256, 45.2804], [19.9252, 45.2844],
            #     [19.9248, 45.2882],
            # ],
            # "Test Field B (Kać area)": [
            #     [19.9398, 45.2782], [19.9498, 45.2785], [19.9502, 45.2742],
            #     [19.9506, 45.2708], [19.9406, 45.2704], [19.9402, 45.2744],
            #     [19.9398, 45.2782],
            # ],
            # "Test Field C (Kać area)": [
            #     [19.9148, 45.2682], [19.9248, 45.2685], [19.9252, 45.2642],
            #     [19.9256, 45.2608], [19.9156, 45.2604], [19.9152, 45.2644],
            #     [19.9148, 45.2682],
            # ],
        }

        FIELD_SOURCE_OPTIONS = [
            "EuroCrops (Slovenia, official)",
            "EuroCrops (Slovakia, official)",
            # "OpenStreetMap",
            # "Fields of the World (ML)",
        ]
        field_sources = st.multiselect(
            "Field boundary source(s):",
            FIELD_SOURCE_OPTIONS,
            default=FIELD_SOURCE_OPTIONS,
        )

        # EuroCrops is loaded first so its fields land first in FIELDS, making one
        # of them the selectbox's default (index 0) pick below.
        FIELD_CROPS = {}
        if EUROCROPS_MODULE_AVAILABLE and "EuroCrops (Slovenia, official)" in field_sources:
            try:
                FIELDS.update(_cached_eurocrops_fields())
                FIELD_CROPS.update(_cached_eurocrops_crops())
            except Exception as exc:
                st.caption(f"⚠️ EuroCrops fields unavailable: {exc}")

        if EUROCROPS_MODULE_AVAILABLE and "EuroCrops (Slovakia, official)" in field_sources:
            try:
                FIELDS.update(_cached_eurocrops_fields_sk())
                FIELD_CROPS.update(_cached_eurocrops_crops_sk())
            except Exception as exc:
                st.caption(f"⚠️ EuroCrops (Slovakia) fields unavailable: {exc}")

        # if OSM_MODULE_AVAILABLE and "OpenStreetMap" in field_sources:
        #     try:
        #         FIELDS.update(_cached_osm_fields((19.90, 19.96, 45.26, 45.30)))
        #     except Exception as exc:
        #         st.caption(f"⚠️ OSM fields unavailable: {exc}")
        #
        # if FTW_MODULE_AVAILABLE and "Fields of the World (ML)" in field_sources:
        #     try:
        #         FIELDS.update(_cached_ftw_fields())
        #     except Exception as exc:
        #         st.caption(f"⚠️ FTW fields unavailable: {exc}")

        field_name = st.selectbox("Field:", list(FIELDS.keys()))
        parcel_4112_polygon = FIELDS[field_name]
        lons = [pt[0] for pt in parcel_4112_polygon]
        lats = [pt[1] for pt in parcel_4112_polygon]
        bbox = (min(lons), max(lons), min(lats), max(lats))
        padded_lon_min, padded_lat_min, padded_lon_max, padded_lat_max = fetch_sat._padded_bbox(
            parcel_4112_polygon, pad_ratio=0.5
        )
        padded_bbox = (padded_lon_min, padded_lon_max, padded_lat_min, padded_lat_max)

        lat_center = (bbox[2] + bbox[3]) / 2
        lon_center = (bbox[0] + bbox[1]) / 2
        with header_placeholder:
            _render_header(
                f"Live Satellite Monitoring: {field_name} — {lat_center:.4f}°N, {lon_center:.4f}°E"
            )

        today = datetime.date.today()
        default_month = today.month - 1
        default_year = today.year
        if default_month == 0:
            default_month = 12
            default_year -= 1

        year_options = list(range(today.year, today.year - 6, -1))

        year_col, month_col = st.columns([1, 1])
        with year_col:
            live_year = st.selectbox("Year:", year_options, index=year_options.index(default_year))
        with month_col:
            live_month = st.selectbox(
                "Month:", list(range(1, 13)), index=default_month - 1,
                format_func=lambda m: calendar.month_abbr[m],
            )

        selection_key = (field_name, live_year, live_month)
        date_from = datetime.date(live_year, live_month, 1).isoformat()
        last_day = calendar.monthrange(live_year, live_month)[1]
        month_end = datetime.date(live_year, live_month, last_day)
        date_to = min(month_end, today).isoformat()

        if field_name in FIELD_CROPS:
            crop_name = FIELD_CROPS[field_name]
            monthly_ndvi = _monthly_ndvi_series(parcel_4112_polygon, padded_bbox, live_year)
            available = {m: v for m, v in monthly_ndvi.items() if v is not None}

            # Fixed to the EuroCrops declaration year (2023), not "last full year":
            # that's the one year we actually know the declared crop was correct.
            # Using a later year risks the field having rotated to a different
            # crop since the declaration, contaminating the benchmark itself.
            benchmark_year = CROP_DECLARATION_YEAR
            benchmark, benchmark_std, benchmark_fields = _crop_benchmark_series(
                crop_name, benchmark_year, exclude_label=field_name
            )
            benchmark_available = {m: v for m, v in benchmark.items() if v is not None}
            correlation, common_months = _curve_correlation(monthly_ndvi, benchmark)
            match_score, band_coverage = _crop_match_score(
                monthly_ndvi, benchmark, benchmark_std, correlation, common_months
            )

            st.markdown(f"**📈 Monthly NDVI — {crop_name}, {live_year}**")

            if available or benchmark_available:
                fig_ndvi, ax_ndvi = plt.subplots(figsize=(3.6, 1.5))
                # Plot months as numeric positions (1-12) with fixed calendar tick
                # labels, not month-abbreviation strings as the x-data directly:
                # matplotlib's categorical axis places string categories in
                # first-appearance order across *all* plotted series, so a month
                # missing from one series but present in another (e.g. no cloud
                # -free benchmark data for Jan) gets appended out of order instead
                # of sorted calendrically — producing a stray line jumping across
                # the whole chart to a misplaced point.
                if benchmark_available:
                    bench_order = sorted(benchmark_available)
                    bench_values = np.array([benchmark_available[m] for m in bench_order])
                    bench_stds = np.array([benchmark_std.get(m) or 0.0 for m in bench_order])
                    ax_ndvi.fill_between(
                        bench_order, bench_values - bench_stds, bench_values + bench_stds,
                        color="#888888", alpha=0.2, linewidth=0, label="±1 std",
                    )
                    ax_ndvi.plot(
                        bench_order, bench_values,
                        color="#888888", linewidth=1.5, linestyle="--",
                        label=f"benchmark {benchmark_year}",
                    )
                if available:
                    month_order = sorted(available)
                    ax_ndvi.plot(
                        month_order, [available[m] for m in month_order],
                        marker="o", color="#1E5631", linewidth=2, markersize=3,
                        label=f"{live_year}",
                    )
                ax_ndvi.set_xlim(0.5, 12.5)
                ax_ndvi.set_xticks(range(1, 13))
                ax_ndvi.set_xticklabels([calendar.month_abbr[m][0] for m in range(1, 13)])
                ax_ndvi.set_ylim(-0.1, 1.0)
                ax_ndvi.tick_params(labelsize=6)
                ax_ndvi.grid(True, linestyle="--", alpha=0.3)
                ax_ndvi.legend(fontsize=5, loc="lower right")
                fig_ndvi.tight_layout(pad=0.3)
                st.pyplot(fig_ndvi, use_container_width=True)
            else:
                st.caption("No cloud-free NDVI data available yet for this year.")

            if match_score is None:
                st.info("ℹ️ Not enough overlapping cloud-free months yet to verify this year against the benchmark.")
            elif match_score >= 75:
                st.success(f"✅ {match_score:.0f}% match with declared crop \"{crop_name}\".")
            elif match_score >= 40:
                st.warning(f"⚠️ {match_score:.0f}% match with declared crop \"{crop_name}\" — worth a closer look.")
            else:
                st.error(f"❌ {match_score:.0f}% match with declared crop \"{crop_name}\" — possible misdeclaration.")

            if match_score is not None and match_score < 75:
                alt_crop, alt_score = _best_alternative_crop(monthly_ndvi, crop_name, benchmark_year)
                if alt_crop is not None and alt_score > match_score:
                    st.warning(f"🔎 Fits \"{alt_crop}\" better ({alt_score:.0f}%).")

            with st.expander("Methodology / caveats"):
                st.caption(
                    "Score = heuristic blend of curve-shape correlation and how often this "
                    "field falls within the benchmark's own ±1 std range — not a calibrated "
                    "statistical probability (no labeled wrong-declaration examples exist to "
                    "calibrate against)."
                )
                st.caption(
                    f"Solid = this field, real Sentinel-2 masked to its own polygon. Dashed + shaded band = "
                    f"benchmark mean ±1 std, from {len(benchmark_fields)} other fields declared as "
                    f"\"{crop_name}\" ({benchmark_year}, same methodology)."
                )
                st.caption(
                    f"⚠️ Crop is per the EuroCrops {CROP_DECLARATION_YEAR} declaration (the last one in "
                    f"this sample) — it may have since rotated to a different crop for {live_year}."
                )
                skipped = sorted(set(monthly_ndvi) - set(available))
                if skipped:
                    st.caption(f"No cloud-free data for: {', '.join(calendar.month_abbr[m] for m in skipped)}.")

    def draw_field_boundary(png_bytes, polygon_coords, image_bbox):
        """Overlays the field's vector polygon on a fetched product PNG.

        image_bbox must be the same (lon_min, lon_max, lat_min, lat_max) the
        image was actually rendered for (the padded bbox, not the tight one),
        or the outline won't line up with the field in the picture.
        """
        image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        points = _project_polygon_to_pixels(polygon_coords, image_bbox, image.size)
        draw = ImageDraw.Draw(image)
        # White halo first so the outline stays visible on both light and dark tiles.
        draw.line(points, fill=(255, 255, 255, 255), width=4, joint="curve")
        draw.line(points, fill=(255, 0, 90, 255), width=2, joint="curve")
        return image

    def render_coords_and_caption(result):
        st.image(result["image"], caption=result["caption"])
        lon_min, lon_max, lat_min, lat_max = result["bbox"]
        st.caption(
            f"Coordinates: {lat_min:.4f}°N – {lat_max:.4f}°N, "
            f"{lon_min:.4f}°E – {lon_max:.4f}°E"
        )

    def render_legend(product):
        if product["legend"] == "ramp":
            gradient_stops = ", ".join(
                f"rgb({r},{g},{b}) {(v + 1) / 2 * 100:.1f}%" for v, (r, g, b) in product["ramp"]
            )
            ticks_html = "".join(f"<span>{t}</span>" for t in product["ramp_ticks"])
            st.markdown(
                f"""
                <div style="margin-top: 2px;">
                    <div style="height: 8px; border-radius: 4px;
                                background: linear-gradient(to right, {gradient_stops});"></div>
                    <div style="display: flex; justify-content: space-between;
                                font-size: 11px; color: #888; margin-top: 2px;">
                        {ticks_html}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        elif product["legend"] == "categorical":
            swatches = "".join(
                f'<span style="display:inline-flex; align-items:center; margin-right:8px; '
                f'font-size:10px; color:#888;">'
                f'<span style="width:9px; height:9px; background:rgb{color}; '
                f'display:inline-block; margin-right:3px; border-radius:2px;"></span>{label}</span>'
                for _, label, color in fetch_sat.SCL_LEGEND
            )
            st.markdown(
                f'<div style="margin-top: 4px; line-height: 1.8;">{swatches}</div>',
                unsafe_allow_html=True,
            )

    def render_product_result(product, result):
        if result is None:
            st.caption("Fetching snapshot...")
            return
        if result["error"]:
            st.error(f"❌ {result['error']}")
            return
        render_coords_and_caption(result)
        render_legend(product)

    with tiles_col:
        COLS_PER_ROW = 3
        products = fetch_sat.PRODUCTS
        for row_start in range(0, len(products), COLS_PER_ROW):
            row_products = products[row_start:row_start + COLS_PER_ROW]
            row_cols = st.columns(COLS_PER_ROW)
            for col, product in zip(row_cols, row_products):
                with col:
                    st.markdown(f"**{product['label']}**")
                    result_key = f"live_prod_{product['key']}_result"
                    cache_key = f"live_prod_{product['key']}_key"
                    with st.container(key=f"live_prod_{product['key']}_box", border=False):
                        placeholder = st.empty()
                        with placeholder.container():
                            render_product_result(product, st.session_state.get(result_key))

                        if st.session_state.get(cache_key) != selection_key:
                            with st.spinner(f"Fetching {product['label']}..."):
                                try:
                                    png_bytes = fetch_sat.fetch_visual_png(
                                        parcel_4112_polygon, date_from, date_to,
                                        product["data_type"], product["evalscript"],
                                        cloud_filter=product["cloud_filter"], sar=product["sar"],
                                    )
                                    overlaid_image = draw_field_boundary(
                                        png_bytes, parcel_4112_polygon, padded_bbox
                                    )
                                    new_result = {
                                        "image": overlaid_image,
                                        "caption": f"{product['label']}: {field_name}, {date_from} → {date_to}",
                                        "bbox": bbox,
                                        "error": None,
                                    }
                                except Exception as exc:
                                    new_result = {"image": None, "caption": None, "bbox": None, "error": str(exc)}
                            st.session_state[result_key] = new_result
                            st.session_state[cache_key] = selection_key
                            with placeholder.container():
                                render_product_result(product, new_result)

# === Everything below is mocked demo data (hardcoded "historical" NDVI,
# a random-noise raster standing in for imagery, a fixed old Parcel #4112
# polygon unrelated to whatever field is actually selected above) — none of
# it reflects the live CDSE/Sentinel Hub data from the section above, so it's
# disabled to avoid mixing real and fake data in the same dashboard.
#
# # HISTORICAL NDVI DATA TIME-SERIES
# months = ["March", "April", "May", "June", "July", "August", "September", "October"]
# historical_ndvi = [0.22, 0.45, 0.82, 0.75, 0.20, 0.18, 0.15, 0.12]
#
# # SPLIT UI INTO TWO EQUAL COLUMNS
# col_left, col_right = st.columns(2)
#
# # === RIGHT COLUMN: MONITORING CONTROLS & AI ENGINE ===
# with col_right:
#     st.markdown("##### 📅 Timeline & Anomaly Controls")
#
#     s_col1, s_col2 = st.columns(2)
#     with s_col1:
#         selected_month_idx = st.slider("Inspection Month:", 0, 7, 2, format="")
#         st.write(f"Selected Target: **{months[selected_month_idx]}**")
#     with s_col2:
#         anomaly_factor = st.slider("Field Condition Factor (1.0 = Normal):", 0.2, 1.2, 1.0, 0.1)
#
#     # FIX: Guaranteed immutable list replication to keep timeline context intact
#     current_year_ndvi = list(historical_ndvi)
#     current_year_ndvi[selected_month_idx] = float(np.clip(historical_ndvi[selected_month_idx] * anomaly_factor, 0, 1))
#
#     st.markdown("---")
#     st.markdown("##### 🧠 Computer Vision Engine Verdict")
#
#     active_ndvi = current_year_ndvi[selected_month_idx]
#     deviation = historical_ndvi[selected_month_idx] - active_ndvi
#
#     if deviation > 0.3:
#         st.markdown(
#             f"""
#             <div style="background-color: #333333; padding: 15px; border-radius: 5px; color: white; border-left: 5px solid #555555;">
#                 <strong>⚠️ WARNING: SIGNIFICANT VEGETATION ANOMALY IN {months[selected_month_idx].upper()}</strong><br>
#                 Current NDVI dropped to {active_ndvi:.2f} (Expected: {historical_ndvi[selected_month_idx]:.2f}).
#                 eAgrar subsidy payouts suspended automatically pending human inspector verification.
#             </div>
#             """,
#             unsafe_allow_html=True
#         )
#     else:
#         st.success(f"✅ VERIFICATION SUCCESSFUL: FIELD STABLE IN {months[selected_month_idx].upper()}")
#         st.info(f"Current NDVI: {active_ndvi:.2f} aligns perfectly with the 5-year historical profile.")
#
# # === LEFT COLUMN: DYNAMIC GIS RASTER CONTAINER ===
# with col_left:
#     st.markdown("##### 🛰️ High-Resolution Target Raster Scan")
#     st.write(f"Center Coordinates: `45.2845° N, 19.9312° E` | Target Month: {months[selected_month_idx]}")
#
#     fig_map, ax_map = plt.subplots(figsize=(7, 5.2))
#
#     np.random.seed(101)
#     raster_bg = np.random.uniform(0.15, 0.25, (100, 100))
#     raster_bg[30:75, 25:75] = active_ndvi + np.random.normal(0, 0.02, (45, 50))
#
#     ax_map.imshow(raster_bg, cmap="YlGn", extent=[19.920, 19.940, 45.275, 45.295], origin="lower", vmin=0.0, vmax=1.0)
#
#     exact_field_polygon = [
#         [19.9248, 45.2882],
#         [19.9348, 45.2885],
#         [19.9352, 45.2842],
#         [19.9356, 45.2808],
#         [19.9256, 45.2804],
#         [19.9252, 45.2844],
#         [19.9248, 45.2882]
#     ]
#     poly_x, poly_y = zip(*exact_field_polygon)
#
#     border_color = "#D2143A" if deviation > 0.3 else "#00FF66"
#     ax_map.plot(poly_x, poly_y, color=border_color, linewidth=3, label="Parcel #4112 Vector Bounds")
#     ax_map.fill(poly_x, poly_y, color=border_color, alpha=0.10)
#     ax_map.scatter([19.9312], [45.2845], color="#1E5631", edgecolor="black", s=150, zorder=5, label="Sensor Node")
#
#     ax_map.set_xlabel("Longitude (°E)", fontsize=9)
#     ax_map.set_ylabel("Latitude (°N)", fontsize=9)
#     ax_map.grid(True, linestyle=":", alpha=0.4, color="black")
#     ax_map.legend(loc="lower left", fontsize=8)
#
#     st.pyplot(fig_map)
#
# # === CONTINUATION OF THE RIGHT COLUMN (Strict Technical Matplotlib Curve) ===
# with col_right:
#     st.write("")
#     fig_chart, ax_chart = plt.subplots(figsize=(10, 2.8))
#
#     ax_chart.plot(months, historical_ndvi, label="5-Year Historical Baseline", color="#888888", linestyle="--", linewidth=2)
#
#     # Isolated slice tracking array safely before passing data nodes
#     visible_months = months[:selected_month_idx+1]
#     visible_ndvi = current_year_ndvi[:selected_month_idx+1]
#
#     ax_chart.plot(visible_months, visible_ndvi,
#             label="Current Season Track", color="#1E5631" if deviation <= 0.3 else "#111111", marker="o", linewidth=2.5)
#
#     ax_chart.scatter(months[selected_month_idx], current_year_ndvi[selected_month_idx], color="black", s=80, zorder=5)
#     ax_chart.set_ylabel("NDVI Value")
#     ax_chart.set_ylim(0, 1.0)
#     ax_chart.grid(True, linestyle="--", alpha=0.3)
#     ax_chart.legend(loc="upper right")
#     st.pyplot(fig_chart)
