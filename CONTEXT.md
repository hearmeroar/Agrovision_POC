# Project context (eAgrar AI-Verification System)

Working notes on what this Streamlit POC does and how it got here — for
picking the work back up, not a polished README (that's still TODO).

## What it is

A demo dashboard that checks whether a farmer's *declared crop* on a field
actually matches what's growing there, using real Sentinel-2 satellite data
— the core use case behind Serbia's eAgrar subsidy-verification program.
Pick a field → see its real monthly NDVI curve → compare it against a
benchmark curve built from *other* fields declared as the same crop → get a
match score (not just yes/no) and, if it doesn't match, a guess at what's
actually growing there instead.

Live: https://agrovisionpoc-3kx4ws7d3h3hazv2zqj26t.streamlit.app
Repo: https://github.com/hearmeroar/Agrovision_POC (public)
Local dev: `streamlit run app.py --server.port 8502` (this is the port the
user runs locally and expects restarted after code changes)

## Why Slovenia/Slovakia, not Serbia

The actual goal is Serbia (RGZ cadastre), but RGZ's REST API
(`rest.geosrbija.rs/api/dkp/v1`) needs an `x-access-token` that was never
requested/obtained (user: "похуй на токен, если технология работает то
токен не проблема" — chasing it isn't the priority right now). So the whole
pipeline is proven on two EU countries where the same *kind* of official
data (LPIS/IACS subsidy-parcel registry, via EuroCrops) is already open:
Slovenia (Prekmurje) and Slovakia (Danubian Lowland near Dunajská Streda) —
both flat Pannonian-basin farmland, same landscape as Vojvodina, similar
crop mix. Swapping in real RGZ data later should be a matter of writing one
more loader module like `eurocrops_fields.py`, not re-architecting anything.

## Field boundary sources

Three were built; only EuroCrops is currently active in the UI (OSM and FTW
are commented out in `app.py`'s source multiselect + loading block, not
deleted — modules still work standalone):

- **EuroCrops (official, active)** — `eurocrops_fields.py`. Real LPIS/IACS
  parcels with a real declared crop + area, from JRC's EuroCropsV2 mirror.
  Two country samples bundled: `sample_fields_eurocrops_si.geojson` (466
  fields) and `sample_fields_eurocrops_sk.geojson` (462 fields). This is the
  only source with known crop declarations, so it's the only one the
  verification engine (below) can run against.
- **OpenStreetMap (disabled)** — `osm_fields.py`. Live Overpass query,
  crowd-traced `landuse=farmland` polygons, no crop info.
- **Fields of the World / FTW (disabled)** — `ftw_fields.py`. ML-model
  (PRUE) field-boundary predictions from Sentinel-2, no crop info, comes
  with a confidence score per field (was often borderline, ~0.5).

## The crop-verification engine (the actual point of this project)

Core files: `app.py` (UI + orchestration), `fetch_sat.py` (CDSE/Sentinel Hub
Process API), `crop_benchmarks.py` + `.json` (benchmark cache),
`build_crop_benchmarks.py` (offline batch precompute), `crop_mapping.py`
(SI↔SK crop-name translation).

1. **Per-field monthly NDVI** (`_monthly_ndvi_series` in `app.py`): calls
   Sentinel Hub once per month, masks to cloud/no-data (`dataMask`) *and* the
   field's own polygon (not just its padded bbox — `_polygon_mask`), means
   the valid pixels. Needed a real bug fix along the way:
   `fetch_sat.NDVI_EVALSCRIPT` didn't set `sampleType`, so Sentinel Hub
   silently auto-normalized to UINT8 instead of returning real NDVI — fixed
   to `FLOAT32`.
2. **Benchmark** (`_crop_benchmark_series`): for the declared crop, takes up
   to `BENCHMARK_FIELDS_PER_CROP` (10) *other* fields declared as the same
   crop — matched by **canonical crop** (`crop_mapping.canonical_crop`), not
   the raw string, so Slovak "Pšenica letná ozimná" and Slovenian "pšenica
   (ozimna)" pool together as one "winter wheat" candidate set instead of
   two disjoint ones. Excludes fields under `MIN_BENCHMARK_FIELD_AREA_HA`
   (0.3 ha — below that, too few clean 10m Sentinel-2 pixels post-masking to
   trust). Always uses **2023**, the EuroCrops sample's actual declaration
   year (`CROP_DECLARATION_YEAR`) — not "last full year" — because that's
   the one year the declared crop is actually known to be correct; a later
   year risks the field having rotated crops since declaration and
   contaminating the benchmark itself.
   - **Sampling bug that got fixed**: `sorted(candidates)[:10]` always
     picked Slovenian labels first (`'I' < 'K'` alphabetically) whenever
     Slovenia alone already had ≥10 eligible fields — silently defeating the
     whole point of pooling for every common crop. Fixed with
     `_balanced_candidate_sample`, which round-robins across country groups.
     **Caveat**: the ~19 benchmarks computed *before* this fix (see below)
     were never recomputed — user explicitly said leave them as-is. So
     wheat/corn/barley/etc. benchmarks are still Slovenia-only in practice
     until/unless someone reruns `build_crop_benchmarks.py`.
   - Mean *and* std are computed per month; std draws a ±1 std "acceptable
     range" band on the chart.
3. **Verdict**: Pearson correlation between the field's curve and the
   benchmark, over overlapping months (`_curve_correlation`), blended with
   how often the field falls inside the benchmark's own ±1 std band
   (`_crop_match_score`) into a single 0–100% score — explicitly labeled in
   the UI as a heuristic, not a calibrated probability (no labeled
   wrong-declaration examples exist to calibrate against).
4. **"Might actually be X" suggestion** (`_best_alternative_crop`): if the
   match score is low, checks the curve against *every other* crop that
   already has a cached benchmark (no new API calls) and surfaces the best
   fit — but skips candidates that are the *same* canonical crop under the
   other country's name (otherwise a Slovak wheat field could get flagged
   against Slovenian wheat purely because of the language difference, which
   is a translation artifact, not a misdeclaration signal).
5. **Disk cache** (`crop_benchmarks.py`/`.json`): benchmarks are expensive
   (10 fields × 12 months = up to 120 real Sentinel Hub calls each), so
   they're persisted to disk with full methodology text embedded per entry.
   `build_crop_benchmarks.py` is the offline batch tool that precomputes
   many at once instead of the normal lazy/on-demand path (first view of
   any new crop computes and caches it there and then).

### Benchmark coverage as of now

18 of 23 Slovenian crops (5 have zero fields ≥0.3 ha, physically can't
benchmark). Slovakia: only the 6 biggest by field count were explicitly
batch-computed (fallow-with-cover, winter barley, sunflower, alfalfa,
triticale, permanent grassland) plus a handful picked up incidentally from
live browsing (corn, winter rapeseed, winter wheat, "Unknown"). ~25 of
Slovakia's 42 distinct crop declarations still have no benchmark and will
compute lazily (~10-60s) the first time someone views a field of that crop.

## Layout / UI notes

Two-column: left = compact controls (field source multiselect, field
picker, year/month, the NDVI chart + verdict, with methodology/caveats
tucked into a collapsed `st.expander`), right = 3 product tiles side by
side (NDVI, True Color, SCL — trimmed down from 12; the other 9
vegetation-index/SAR variants are commented out in `fetch_sat.PRODUCTS`,
not deleted, since none of them fed any actual computation). The CDSE
token-exchange diagnostic is a collapsed expander, not a headline subheader
— it's a utility, not the point of the page. Header subtitle shows the
actually-selected field + its real centroid coordinates (used to show a
hardcoded "Vojvodina, Land Parcel #4112" claim — removed along with an
entire hardcoded/random-noise "historical NDVI" demo section that predated
the real pipeline).

## Known gaps / honest caveats already surfaced in the UI

- Crop declaration is from **2023**; by the time you're looking at 2025/2026
  NDVI, the field may have legitimately rotated to a different crop —
  that's *not* the same thing as a fraudulent declaration, and the UI says so.
- Match score is a heuristic (correlation + band-coverage blend), not a
  calibrated probability.
- Pre-fix benchmarks (most Slovenian ones) don't yet benefit from SI/SK
  pooling — only recompute if asked.
- Serbia itself has zero real data in this app right now — RGZ token still
  not obtained, deliberately deprioritized.

## Repo housekeeping done

- `.gitignore`: excludes `.streamlit/secrets.toml` (real CDSE credentials,
  git-ignored, must be pasted into Streamlit Cloud's Secrets panel
  separately), `__pycache__`, and the two huge FTW reference files
  (`ftw_fields_N45E019_2024.parquet`/`.geojson`, 33MB/280MB — not used by the
  running app, kept locally only as a "how would you get more data" sample).
- `requirements.txt`: streamlit, numpy, matplotlib, Pillow, requests,
  tifffile.
- User preference saved to memory: push to `origin/main` automatically for
  routine changes, no need to ask each time (see
  `~/.claude/projects/-Users-alex-Documents-Agrovision-POC/memory/feedback_git_push.md`).

## Plausible next steps (not started)

- README.md for the repo (outline was agreed but deprioritized in favor of
  adding Slovakia + the crop mapping).
- Recompute the ~19 pre-fix benchmarks with balanced SI/SK sampling, if ever
  wanted.
- Batch-precompute the remaining ~25 uncached Slovak crops.
- A third EuroCrops country (candidates already scoped: Czech Republic,
  Bulgaria — see the full EuroCropsV2 country/region list surfaced earlier
  in this conversation) if more crop diversity/robustness is wanted.
- Eventually: real Serbian RGZ cadastre data, once the token exists — should
  slot in as a fourth `*_fields.py` loader without touching the
  verification-engine files.
