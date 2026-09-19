# Recreate Wildfire Atlas: prompt for Astra

Copy the prompt below into a new Astra session. Supply the sanitized
`training-csvs-2026-05-11_to_2026-08-22/` directory as local files. The prompt
works without this repository, but exact reproduction of the fitted model and
geospatial playback also requires the optional artifacts listed below.

This document belongs to `docs/`, outside the checksummed public CSV release.
It describes the application's September 2026 implementation, not a promise
that a new training run will reproduce its historical scores.

---

## Prompt begins

You are implementing **Wildfire Atlas**, a North American wildfire research
application. Recreate its **React frontend, FastAPI backend, and model layer**
in the current workspace. Deliver working code, run meaningful tests, and
document how to start and use it. Do not stop at a plan or UI mockup.

The product lets someone place fires or load satellite detections, simulate
spread, compare historical observations, and inspect fire/terrain/vegetation
features. Its main interface is a responsive interactive map with playback.

### 1. Scope and available inputs

Build the application and model training/inference code. Do not rebuild the
bulk data-collection pipeline, collection notebooks, weather backfills,
national road downloads, or original multi-round model-search history.
Read existing data through small provider interfaces. Transient live FIRMS
requests and local runtime tile preparation are in scope; durable collection
of new source datasets is not.

Look for the supplied dataset at
`releases/training-csvs-2026-05-11_to_2026-08-22/`, or accept its location through
`TRAINING_CSV_DIR`. It contains:

| File | Rows | Meaning |
| --- | ---: | --- |
| `candidate_examples.csv` | 500,364 | Candidate identities, observed fire features, terrain, weak labels, original chronological split and lineage |
| `weather_features.csv` | 500,364 | Hourly historical ECMWF IFS weather and mapping provenance |
| `weather_history.csv` | 500,364 | Six-hour and 24-hour weather summaries |
| `directional_features.csv` | 500,364 | Observed nearby-fire geometry and wind projections |
| `vegetation_features.csv` | 500,364 | Retrospective NALCMS/MOD44B cover, quality, age and lineage |
| `landscape_features.csv` | 500,364 | Corrected fuel/road pilot features and missingness |
| `incident_assignments.csv` | 500,364 | Incident, region and evaluation cohort assignments |
| `unscored_positives.csv` | 35,265 | Diagnostic positives outside candidate support; exclude from fitting |

Read the supplied `README.md`, `manifest.json`, `metadata/` and `SHA256SUMS`
before interpreting the data. Verify every listed checksum and the expected
schemas. Treat source paths as provenance, not paths that must be present on
this machine. Packaged source-manifest hashes and original source-manifest
hashes are distinct following privacy sanitization.

The dataset is approximately 4.17 GB uncompressed. Use projected columns,
explicit dtypes, streaming or bounded batches. Do not duplicate the entire
dataset for every experiment or load it into a browser/API request handler.

Optional inputs, if supplied separately:

- Original model source and trusted completed model bundles/run manifests.
- Retained FIRMS historical archive with coverage and integrity manifests.
- ETOPO terrain blocks and the shared grid/sentinel definitions.
- Natural Earth land/lake mask geometry and source metadata.
- NALCMS/MOD44B rasters or compatible vegetation stores and manifests.
- Native fuel/road pilot bundles or a completed indexed road GeoParquet
  archive plus local NALCMS rasters for detailed landscape playback.
- Original frontend source or screenshots for closer visual reproduction.

CSV training features cannot reconstruct raw detections, arbitrary-cell
terrain, satellite availability history, road geometry, or 30 m fuel rasters.
Do not invent these from candidate labels. Implement all provider interfaces
and missing-resource behavior; enable real-data capabilities when their
resources exist. Use clearly labeled fixtures only in tests or an explicit
demo mode. Do not silently replace missing trained models with random scores.

When original code and trusted weights are supplied, preserve their import
compatibility, feature transforms and model behavior. When only CSVs are
supplied, train a new, versioned compatible model and report its actual results;
do not claim exact reproduction of the original weights or scores. Continue
independent implementation if optional sources are absent, and list the exact
missing resources at completion.

### 2. Architecture and runtime

Use Python, FastAPI/Pydantic, React, Leaflet, and scikit-learn histogram
gradient boosting. The reference frontend uses React 19, Leaflet 1.9.4,
esbuild, and Node 22 or newer. Choose compatible dependencies and lock the
versions actually tested. NumPy/pandas, pyproj, Shapely and joblib support the
model; rasterio and GeoParquet readers belong with optional spatial providers.

Organize responsibilities approximately as follows:

```text
frontend/
  src/{App,FireMap,Inspector,HistoricalInspector,CellList}.jsx
  src/{useScenario,api}.js
  src/style.css
  build.mjs
src/wildfire_data/
  core/                 # Grid, UTC handling, hashing and artifact contracts
  model/
    features/           # Explicit feature schemas and pure feature transforms
    training/           # Dataset loading, training, calibration and evaluation
    loading.py          # Trusted, checksum-verified model loading
    recursive_transition.py
    spread.py           # Coarse interactive preview
    local_spread.py     # Optional detailed fuel-patch engine
  web/
    app.py              # App factory, lifecycle, routes, static assets
    schemas.py
    serialization.py
    providers/          # Local sources and transient FIRMS integration
    static/             # Generated frontend assets and license notices
tests/{core,model,web}/
config/                 # Portable examples; no real credentials
README.md
```

The model must run without importing FastAPI or React infrastructure. Supply
terrain/weather/vegetation through injected providers. Separate training from
inference; startup loads a model, never starts fitting. Expose an app factory
with injectable model and providers for deterministic API tests.

Build the frontend into FastAPI's static directory and serve it from `/` on
the same origin. Provide build/watch scripts. Keep UI state in the browser;
each tab has its own scenario. No database, login system, server-side scenario
clock or cloud deployment is required. Keep temporary caches bounded and close
file readers on shutdown. Run expensive inference/raster work outside the
ASGI event loop, with explicit concurrency admission around shared caches.

### 3. Product design and frontend behavior

Recreate a restrained map workspace branded **Wildfire Atlas**, with subtitle
**SPREAD EXPLORER** and a North America research-preview label. Use a white
top bar, approximately 318 px pale sidebar on desktop, and a large map. Use
warm off-white `#fafbf7`, forest green `#234e40`, dark ink `#223731`, orange
`#e37040`, and subtle borders. Prefer local/system fonts and light surfaces.
The sidebar headline is “A spark. A possible future.” Preserve hierarchy,
spacing and legibility on mobile; avoid a generic dashboard of charts.

Required controls and views:

- **Place a fire** and **FIRMS detections** source selectors.
- Starting intensity slider, 0–100%, default 70%, step 5%; map-click placement
  and a latitude/longitude form. Merge repeated coarse-cell seeds by maximum
  intensity. Allow additions only to an initial placed scenario; otherwise reset.
- A simulation selector for **Existing 1 km model**, optional fixed landscape
  pilots such as Edson/Boulder, and **Expanding landscape · U.S./Canada**.
  Disable unavailable modes with a clear reason. Fine landscape ignition does
  not use the coarse intensity slider; disable it in that mode.
- Live FIRMS loading, historical date selection, and all-region/visible-area
  scope. The coarse live default extent is `[-179, 24, -52, 84]`, independent
  of zoom. Expanding landscape loads should use a bounded visible area and
  explain their capacity limits.
- Active, burned and newly ignited counts, current origin/valid timestamps,
  busy/error status and fit-to-scenario control.
- Separate layers for orange active fire, dark burned cells, optional candidate
  scores, purple historical-observation rings, and the optional basemap.
  Fine landscapes add active/burned GeoJSON polygons, roads and a dashed
  loaded-domain outline. Preserve polygon holes.
- A floating playback panel with Play/Pause, single step, Reset, timeline,
  and 1/3/5/10-second playback intervals; default three seconds after a request
  completes. Normal steps are 12 hours; historical comparison steps are 24 hours.
- A cell inspector with coordinates, cell ID, state/source, intensity, fuel,
  probability, observed brightness/counts and available vegetation evidence.
  Display missing values honestly. Historical observations get an observation
  inspector, not synthetic intensity/fuel or prediction probabilities.
- A filterable, paginated keyboard-accessible cell list that includes offscreen
  cells. Clicking a cell pauses and focuses its inspector; closing restores
  focus. Large map views may group markers for display, but must not truncate
  model inputs. Anchor a group to an actual member cell, not an offshore mean.
- Help/data/privacy dialog, source attribution, basemap toggle, keyboard labels,
  visible focus, skip links, live status, sufficient contrast and reduced motion.
  Explain that basemap requests reveal viewed tiles and network metadata to
  the provider. No third-party analytics or remotely loaded fonts.

Playback correctness is essential. Keep a rolling history of 128 complete
frames. Pausing immediately freezes the display even if inference is pending.
Use AbortController plus generation/request IDs; discard stale responses after
pause, reset, history navigation, source/mode changes and tab visibility changes.
Never overlap playback requests. A failed request preserves the last complete
frame. Scrubbing replays stored states; resuming replays saved future frames
before extending the latest state. Discarding display history must not discard
the current burned-cell mask. Space toggles playback only with body focus,
not while operating a form, dialog, map control or button.

Show an accurate research limitation for the selected mode: the coarse map
preview has no weather inputs; the local fine simulator uses explicit,
uncalibrated scenario assumptions. Do not imply evacuation or emergency
decision support, or display invented accuracy claims.

### 4. Backend API and state contract

Implement these routes with validated JSON and documented response schemas:

| Route | Request and behavior |
| --- | --- |
| `GET /api/config` | Model readiness/error, supported modes, source availability, bounds, historical dates, transition version, steps and request limits |
| `POST /api/seed` | `{ignitions: [{latitude, longitude, intensity}]}`; return an initial coarse frame |
| `POST /api/step` | `{state, origin_at, historical?}`; advance the submitted scenario |
| `POST /api/firms` | Optional `{west, south, east, north}`; load live observations and seed |
| `POST /api/firms/historical` | `{date, bounds}`; load a retained UTC observation day and seed once |
| `POST /api/vegetation` | `{cell_id, origin_at, simulation_at}`; return available evidence/coverage for inspection |
| `POST /api/local/seed` | Seed request plus `region`; optional fixed landscape mode |
| `POST /api/local/step` | `{state, origin_at}`; optional fixed landscape mode |
| `POST /api/landscape/seed` | Seed request; optional expanding landscape mode |
| `POST /api/landscape/step` | `{state, origin_at, historical?}`; advance expanding state |
| `POST /api/landscape/firms` | Bounds; initialize an expanding scenario from live observations |
| `POST /api/landscape/firms/historical` | Date and bounds; initialize an expanding historical scenario |

A coarse `state` contains `step_index`, `active_cells`, `burned_cell_ids`.
Each active cell has `cell_id`, `intensity`, `fuel_remaining`,
`remaining_active_steps`, and `observation_age_hours`. Observed FIRMS cells
also preserve `detection_count`, `bright_ti4_max`, `bright_ti4_mean`, and
`platform_count` together. Reject incomplete aggregate groups, duplicate cells,
active/burned overlap, invalid IDs, nonfinite values and unknown fields.

A frame contains `state`, immutable `origin_at`, derived `valid_at`,
`elapsed_hours`, `points`, `active_count`, `burned_count`, `new_ignition_count`,
`finished`, `extinct`, `terrain_missing_count`, and `metadata`. Points expose
cell ID, latitude/longitude, active/burned/candidate status, nullable intensity,
fuel and probability, source and available observation details. JSON must never
contain NaN/Infinity; serialize missing numeric response values as null.

Historical frames also contain `{start_date, date, bounds, points,
detection_count, cell_count}` under `historical`. A historical step sends the
original `{start_date, bounds}`. Fine frames additionally contain perimeters,
roads, domain coverage and a distinct versioned state schema. Expanding state
pins profile identity, incident identity, geographic ignitions, loaded tile
coordinates/checksums and step index; reject mismatched replay assets.

Normalize timezone-aware timestamps to UTC and require representable dates.
Use canonical IDs with bounded indices. Enforce 8 MiB bodies, a 15-second body
arrival timeout, up to 500 seed points, 10,000 coarse active cells and 100,000
burned IDs. Reject oversized scenarios explicitly rather than truncating them.
Use bounded concurrency, sanitized errors, retryable 429/503 responses and
`Retry-After`. The reference admits eight API requests per process and
serializes model/provider work where shared caches require it.

Use trusted-host configuration, appropriate security headers and same-origin
browser request checks. These controls are not authentication. Disable public
interactive API docs by default; document the routes locally. Keep credentials
on the server, never accept model file paths from arbitrary API callers, never
return traceback/local-account paths, and never load user-uploaded joblib files.
Checksums establish integrity, not trust in a serialized estimator's origin.

### 5. Spatial and temporal contracts

Use a 1,000 m equal-area grid in `ESRI:102008`, with WGS84 display coordinates.
Canonical IDs are `naea-1km:x=<integer>:y=<integer>`. Project longitude/latitude
with explicit axis ordering; compute indices using mathematical floor, including
negative coordinates. Cell centroid is `(index + 0.5) * 1000` in projected
metres. Do not substitute degree rounding or collection tile IDs.

The prediction horizon is 12 hours. The training date range is May 11 through
August 22, 2026 **by source snapshot date**. Anchors/cutoffs and target endpoints
may cross UTC calendar boundaries. Preserve those columns and their semantics.

Join sidecars one-to-one by `example_id`, assert complete ID sets and shared
identity fields, and preserve the original targets and assignments. A cell may
appear at multiple times, so `cell_id` alone is not a valid training join key.
Do not make observation periods current merely because a file was copied later.

### 6. Model training and offline weather model

Implement a real training CLI and held-out evaluation, using supplied CSVs.
`target_newly_burned_12h` is the binary target. A zero is a weak-negative proxy,
not observed absence of burning; FIRMS and FEDS labels share satellite evidence.
Keep `unscored_positives.csv` out of binary training. Preserve missing features
as missing with their flags; do not turn missing wind into calm or missing
rain into zero. Never fit every numeric metadata column.

The base release has 19 allowed features. The coarse frontier contract uses
the following 13, in exactly this order:

```text
firms_local_3x3_has_detection
firms_local_3x3_detection_count
firms_local_3x3_bright_ti4_max
firms_local_3x3_bright_ti4_mean
firms_local_3x3_platform_count
firms_local_3x3_hours_since_last_detection
firms_local_3x3_active_cell_count
terrain_valid
terrain_elevation_m
terrain_slope_degrees
terrain_aspect_defined
terrain_aspect_sin
terrain_aspect_cos
```

Train/calibrate/evaluate on distinct declared cohorts from `incident_split`,
with applicable eligibility and frontier filters; observed frontier rows have
`firms_center_has_detection == 0`. Keep unassigned rows out of these fits.
For a separate original-style 19-feature baseline, preserve whole
`source_snapshot_time` groups under `dataset_split`. Do not randomly divide
neighboring cells or mix chronological and incident-based split contracts.
Reproduce extra time/eligibility restrictions from supplied original protocols
when available; otherwise document the newly defined protocol before scoring.

Report PR-AUC (average precision), ROC-AUC, Brier score, ECE, precision, recall,
cohort sizes/class balance and chosen threshold. Fit calibration on its own
cohort; tune decisions only on development data. Record seeds, dependency
versions, ordered columns, transforms, cohort IDs, source digests and metrics
in a completed model manifest. Save trusted local artifacts and verify reload
predictions. Do not copy historical metrics into a new report.

Also implement the separate **offline weather classifier**. Its reference
policy blends 25% weather-model probability with 75% matched geometry-model
probability, with a global cutoff of 0.15. Both components are histogram
gradient-boosting classifiers: 300 iterations, learning rate 0.04, seven leaves,
minimum 200 samples per leaf, L2 regularization 30, and separate sigmoid
calibration on the same calibration cohort. Persist the random seed you use.

The geometry component uses the 13 frontier inputs plus these eight:

```text
fire_5x5_detection_count
fire_5x5_active_cell_count
fire_5x5_recent_12h_count
fire_nearest_detection_km
fire_nearest_cell_km
fire_nearest_detection_age_hours
fire_distance_weighted_detection_count
fire_recent_fraction
```

The weather component adds current relative humidity, precipitation, derived
wind speed and VPD, all three `wind_*` fields from the directional sidecar,
`weather_rain_6h_mm`, and the five six-hour wind-history fields. Read their
exact spelling/order from the manifests. This gives 34 inputs. Compute:

```text
wind_speed_m_s = hypot(weather_wind_u_10m, weather_wind_v_10m)
vpd_kpa = 0.6108 * exp(17.27*T/(T+237.3)) * (1 - RH/100)
```

Here T is temperature in °C, RH is percent, and wind components are east/north
in m/s. Preserve the meteorological wind-direction convention in transforms.
Do not use the poorly covered 24-hour history columns in this reference policy.
The original weather fit had 20,940 observed frontier training rows and 3,403
calibration rows; treat these as reproduction diagnostics, not a reason to
silently discard arbitrary rows until counts match.

This weather policy is an observed-row offline classifier, **not the map's
recursive model**. Provide a batch prediction/evaluation command. Do not wire
it into arbitrary recursive cells without supplying and validating all required
features there. Historical analysis weather is not an issued forecast.

Vegetation can support an explicitly separate ablation. The landscape sidecar
has only 484 eligible land-cover rows and zero eligible road rows for this
historical range. Its training support gate fails: do not fit a road-aware
model from fabricated zeros or claim roads were learned from this release.

### 7. Coarse map simulation

When available, load the original calibrated incident model, selecting pass 1
or pass 2 from a completed trusted run manifest, and wrap it in the coarse
preview transition. Otherwise supply the newly trained frontier model through
an explicitly versioned adapter and validate its observed/synthetic feature
handling. A recreated adapter is not evidence of identical legacy predictions.

The reference preview is `water-barriers-finite-fuel/v3`:

- Enumerate all reachable eight-neighbor frontier cells and score in bounded
  batches. Preserve burned masking and the classifier's calibrated probability
  and threshold. Do not reintroduce the old 50% or 128-new-cell growth quotas.
- An eligible cell ignites only when its score meets the threshold and a stable
  deterministic uniform draw is less than that score. Key draws by version,
  step index and canonical cell ID, not process-randomized hashing. Preserve
  the original draw function if supplied; otherwise version the replacement.
  The reference draw uses the string
  `water-barriers-finite-fuel/v2:{step_index}:{cell_id}` and computes
  `int(sha256(key.encode()).hexdigest()[:13], 16) / 16**13`.
- Cells begin with one unit of fuel and consume 0.5 per 12-hour step regardless
  of intensity. Surviving intensity cannot exceed remaining fuel. Below 0.05,
  the cell is burned and cannot reignite. A fresh ignition inherits the strongest
  connected neighbor's intensity and consumes its fresh fuel on the next step.
  `fuel_remaining`, not a stale lifetime counter, governs burnout.
- FIRMS and rendered evidence retain their real age and expire outside the
  3–24-hour eligibility window. Do not refresh an active cell's observation
  timestamp at every step. Keep synthetic renderer policy explicit and tested.
- Generalized land/lake geometry checks full cell footprints and connecting
  paths, including diagonals. The reference mask is Natural Earth 5.1.2 at
  1:10 million scale and misses fine waterways. Missing mask coverage is not
  confirmed land. Reject unsupported placement or return an explicit unavailable
  capability when the required mask is absent.
- Coarse playback has no artificial 96-hour cutoff. Empty states remain
  extinct; continuation is bounded by request limits and representable dates.
  Evaluation beyond the original 12–96-hour interval is not established.

Use identical feature contracts for training and inference. Distinguish
observed brightness/counts from rendered synthetic evidence. A supplied terrain
provider samples arbitrary cells; a CSV-only lookup must report missingness
outside its actual coverage. Never infer weather/terrain from future labels.

### 8. FIRMS and historical comparison

Live FIRMS uses three NRT VIIRS feeds: `VIIRS_SNPP_NRT`, `VIIRS_NOAA20_NRT`,
and `VIIRS_NOAA21_NRT`. Keep the API key in server environment variables
`NASA_FIRMS_API_KEY` or `MAP_KEY`. Request two calendar days, parse/stream CSV,
then apply the precise acquisition interval 3–24 hours before the preview origin.
No brightness threshold or silent cell-count truncation. Aggregate to grid
cells while retaining detection count, max/mean TI4 and platform count.

All feeds must parse successfully before replacing the scenario. Valid
header-only data is an empty feed; authentication, malformed data, unavailable
archive and network errors are not empty-fire states. Report observations
excluded by lag/water rules. Preserve observed brightness while mapping it to
explicit synthetic seed intensity, with a 10% minimum for eligible live seeds.
Redact credential-bearing URLs in all logs/errors. Cache up to 16 previews for
five minutes, preserve their original timestamp, and apply a ten-second cooldown
to uncached requests. Do not write these transient requests into a source archive.

Historical loading reads a supplied archive, without NASA credentials or
downloads. The reference UI supports May 11–August 21, 2026, distinct from the
training range ending August 22. Verify complete/empty-confirmed coverage and
artifact hashes for all three feeds; cache a bounded number of decoded days.

Loading day D displays all of that UTC day's observations and sets prediction
origin to midnight after D. Seed only eligible observations within the 3–24-hour
window at that origin. Each historical step performs two normal 12-hour steps
and loads the next day's purple observations. **Never reseed predictions from
later observations.** Commit the paired frame only if both operations succeed.
Keep loaded bounds fixed while panning; history restores matching observations.
Validate the original cutoff and even step index. Stop at August 21; fine mode
also respects its own 96-hour limit. Candidate CSV rows alone do not constitute
the archive needed for this mode.

### 9. Detailed fuel and road simulation

Implement this as an explicitly experimental engine separate from the learned
coarse classifier. Make it usable with supplied local geometry/raster providers
and small deterministic fixtures. It must not turn coarse probabilities into
metres/minute or advertise learned road-crossing behavior.

Use approximately 30 m potential-fuel cells split into vegetation patches by
vector road surfaces. Track within-cell travel/arrival, ignition time, active
residence and burned status. Connectivity alone must not ignite an entire
connected forest instantaneously. Produce active and burned Polygon/
MultiPolygon output with holes plus 1 km summaries. Respect alternative paths,
road cuts, tile seams, nonfuel/unknown classes, bridges/tunnels and distinct
carriageways. Urban mixtures/buildings are unsupported, not uniformly empty fuel.

Reference uncalibrated scenario defaults: forest classes 0.5 m/min; grassland
2; shrubland, cropland and other vegetation 1; wetland 0.25; active residence
120 minutes. Constant east/north wind defaults to zero, with wind coefficient
0.08. Mapped-surface roads lacking measured width use an explicitly disclosed
6 m assumption. Optional spotting defaults off. These are scenario parameters,
not physically validated constants; terrain remains flat in this engine.

Expanding mode builds aligned 3 km × 3 km tiles with a 250 m road-query halo
from local rasters and a pinned offline road archive. Never download roads at
simulation time. Cache verified tiles atomically and reuse shared raster readers.
Extend when the front approaches the boundary; preserve original ignitions,
wind anchor and travel accounting across tile seams. A versioned replay may
recompute from the original seeds after tile growth; it must not restart fire
age or erase previous burns. Missing/corrupt road partitions are explicit errors.

Reference limits: 500 starting patches, 24 tiles/216 km², 250,000 compiled
patches, eight 12-hour steps/96 hours. Satellite seeding selects one supported
vegetation patch nearest each observed coarse-cell center and reports unsupported
cells; this position is an assumption, not a measured 30 m ignition. Capacity
errors preserve the previous frame. Do not drop seeds, silently coarsen the mesh,
or switch engines. First preparation may take minutes: expose useful status,
allow cancellation in the UI, and keep completed cache work reusable. A browser
abort need not forcibly interrupt synchronous server work.

The current road snapshot can support a labeled retrospective historical
scenario, but cannot be represented as historically eligible May–August
training evidence. Do not derive fine fuel or roads from their sparse training
sidecar. In a CSV-only checkout, implement and test this engine with fixtures
and show real-data mode as unavailable until its optional sources are restored.

### 10. Verification and delivery

Implement and run focused tests that establish:

1. Grid round trips, negative-coordinate flooring, canonical IDs, UTC checks,
   stable draws, calibrated artifact reload and ordered feature contracts.
2. Checksum/schema failures, duplicate/missing sidecar IDs, identity mismatches,
   date boundaries, no train/holdout leakage, preserved missingness, and exclusion
   of diagnostic positives/unassigned cohorts from the applicable fits.
3. Seed merging, evidence aging, finite-fuel burnout, no reignition, diagonal
   water barriers, deterministic state replay and empty-state continuation.
4. API validation, bounded requests, sanitized unavailable-resource/provider
   errors, complete source-feed handling and protected shared caches.
5. Historical daily alignment, fixed bounds, two-step advancement, no reseeding,
   end-date limits and atomic prediction/observation frame commits.
6. Browser placement, inspection, pagination, live/historical loading, map
   layers, keyboard focus, mobile reflow, timeline replay and pause/reset races
   with delayed responses. Check beyond 96 hours for the coarse engine and
   rolling history beyond 128 frames using controlled fixtures where practical.
7. Fine-mode roads within a coarse cell, travel around barriers, holes, tile
   seams, domain growth, capacity failures and version/checksum replay rejection.

Run Python tests, build the React assets, and exercise the application with
Playwright when available. Distinguish real-data integration checks from test
fixtures. Run actual training/evaluation when the supplied CSVs are accessible;
do not claim the full pipeline passed from a tiny fixture alone. If a check is
blocked by absent artifacts, name the missing input and finish independent work.

Deliver a complete repository with portable configuration examples, a README,
dependency locks, training/evaluation/prediction commands, provider contracts,
actual test results and a short limitations document. The README must include:

```bash
# Equivalent commands are acceptable if documented and tested.
cd frontend
npm ci
npm run build
cd ..
PYTHONPATH=src OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  python -m uvicorn wildfire_data.web.app:app --host 127.0.0.1 --port 8000
```

Document model/data configuration using `WILDFIRE_RUN_MANIFEST`,
`WILDFIRE_MODEL_PASS`, `WILDFIRE_DATA_ROOT`, optional vegetation/landscape
configuration and `WILDFIRE_ALLOWED_HOSTS`, or clearly document any compatible
replacement. Use placeholders for secrets and repository-relative paths.
Keep original datasets immutable, exclude credentials and personal filesystem
paths from deliverables, retain third-party notices, and do not claim source
redistribution permissions that have not been established.

Make routine implementation choices autonomously. Start by inventorying the
inputs and writing a short implementation plan, then build and verify the
application. Finish by reporting what runs, how to start/train it, measured
test/evaluation results, and any capabilities awaiting optional data. Do not
publish or deploy the repository as part of this task.

## Prompt ends
