import io

import streamlit as st
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw

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


@st.cache_data(ttl=3600, show_spinner="Loading field boundaries from OpenStreetMap...")
def _cached_osm_fields(bbox):
    return osm_fields.fetch_osm_fields(bbox)


@st.cache_data(show_spinner="Loading field boundaries from Fields of the World...")
def _cached_ftw_fields():
    return ftw_fields.load_ftw_fields()


@st.cache_data(show_spinner="Loading official EuroCrops (Slovenia) field boundaries...")
def _cached_eurocrops_fields():
    return eurocrops_fields.load_eurocrops_fields()

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
st.subheader("🛰️ CDSE Live Connection Status (OAuth2)")
if not CDSE_MODULE_AVAILABLE:
    st.warning("`fetch_sat.py` module not found or `requests` is not installed.")
else:
    if st.button("Test CDSE Token Exchange"):
        try:
            fetch_sat.get_access_token(force_refresh=True)
            st.success("✅ Access token obtained. CDSE credentials are valid.")
        except Exception as exc:
            st.error(f"❌ {exc}")

    import calendar
    import datetime

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

    FIELD_SOURCE_OPTIONS = ["OpenStreetMap", "Fields of the World (ML)", "EuroCrops (Slovenia, official)"]
    field_sources = st.multiselect(
        "Field boundary source(s):",
        FIELD_SOURCE_OPTIONS,
        default=FIELD_SOURCE_OPTIONS,
    )

    if OSM_MODULE_AVAILABLE and "OpenStreetMap" in field_sources:
        try:
            FIELDS.update(_cached_osm_fields((19.90, 19.96, 45.26, 45.30)))
        except Exception as exc:
            st.caption(f"⚠️ OSM fields unavailable: {exc}")

    if FTW_MODULE_AVAILABLE and "Fields of the World (ML)" in field_sources:
        try:
            FIELDS.update(_cached_ftw_fields())
        except Exception as exc:
            st.caption(f"⚠️ FTW fields unavailable: {exc}")

    if EUROCROPS_MODULE_AVAILABLE and "EuroCrops (Slovenia, official)" in field_sources:
        try:
            FIELDS.update(_cached_eurocrops_fields())
        except Exception as exc:
            st.caption(f"⚠️ EuroCrops fields unavailable: {exc}")

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

    year_col, month_col = st.columns([1, 2])
    with year_col:
        live_year = st.selectbox("Year:", year_options, index=year_options.index(default_year))
    with month_col:
        live_month = st.select_slider(
            "Month for live snapshot:",
            options=list(range(1, 13)),
            value=default_month,
            format_func=lambda m: calendar.month_abbr[m],
        )

    selection_key = (field_name, live_year, live_month)
    date_from = datetime.date(live_year, live_month, 1).isoformat()
    last_day = calendar.monthrange(live_year, live_month)[1]
    month_end = datetime.date(live_year, live_month, last_day)
    date_to = min(month_end, today).isoformat()

    def draw_field_boundary(png_bytes, polygon_coords, image_bbox):
        """Overlays the field's vector polygon on a fetched product PNG.

        image_bbox must be the same (lon_min, lon_max, lat_min, lat_max) the
        image was actually rendered for (the padded bbox, not the tight one),
        or the outline won't line up with the field in the picture.
        """
        image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        lon_min, lon_max, lat_min, lat_max = image_bbox
        width, height = image.size
        points = [
            (
                (lon - lon_min) / (lon_max - lon_min) * width,
                (1 - (lat - lat_min) / (lat_max - lat_min)) * height,
            )
            for lon, lat in polygon_coords
        ]
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

    st.caption(
        f"Showing all {len(fetch_sat.PRODUCTS)} available products — each panel is a separate "
        "Sentinel Hub request, so switching month/year/field re-fetches all of them."
    )

    COLS_PER_ROW = 4
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
