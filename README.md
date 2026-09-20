# Flickr-Flow · Wildfire Atlas

An incremental, standalone recreation of the React frontend, FastAPI backend,
and wildfire models. This repository runs with `src` alone on the Python
path; it does not import another application's source. Original spatial and
prediction algorithms are ported to preserve trusted-model compatibility.
Runtime readers and startup source preparation live in `providers/`; the bulk
training-data collection pipeline is excluded.

The runtime separates API routes, resource lifecycle, and portable
settings. Existing model and state contracts remain supported. The frontend
retains the map, placement, FIRMS, historical comparison, inspection, detailed
landscapes, and timeline controls. Timeline transitions and request cancellation
are separate tested modules; all three reconstruction milestones are implemented.

## Run

For the map-based desktop application, see
[the desktop quickstart](docs/desktop-quickstart.md). Its separate `WildfireAtlas`
build opens a native window, automatically falls back to an offline overview,
and resolves polygon simulation coverage from the ignition location. Hinton and
Black Hawk appear as installed regions on one map, not simulator choices.
Online placements elsewhere use the explicitly labelled 1 km research model;
its grid-cell polygons are not fine-scale fuel-patch perimeters.
The earlier `WildfirePlanner` package remains the planner-only pilot.

Run these commands from the repository root. Python 3.14.4 and Node 24.18.1
were used for verification; the frontend requires Node 22 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
```

An existing compatible Python environment can also be used. Build the frontend
and start the API:

```bash
cd frontend
npm ci
npm run build
cd ..
PYTHONPATH=src OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  python -m uvicorn wildfire_data.web.app:app --host 127.0.0.1 --port 8000
```

`node frontend/build.mjs` is equivalent to the build script when dependencies
are already installed. Compiled assets are included. Open localhost on port
8000. When the verified pilot packs are installed, startup uses the prepared
polygon configuration without data preparation. The legacy configuration can
prepare source data, with progress in the server log. A new checkout also
needs trusted model artifacts: use the training commands below with the supplied
CSV release. The server can serve the interface without a model, but reports
the model unavailable and disables coarse simulation until configured.

After pulling, rebasing, or resetting the checkout, restart the API and reload
the page. A running Python process keeps its imported code while serving the
updated frontend files; an older API can return fire counts without the polygon
geometry the current map needs. For automatic Python reloads during development,
add `--reload --reload-dir src` to the uvicorn command above. Rebuild the frontend
after editing its source.

## Local resources

With `data/planning-packs-v1/index.json` installed, the web app defaults to
`config/local_spread_prepared.json`: bounded 100 m polygon scenarios in Black Hawk,
Colorado and Hinton, Alberta. Ignition locations select the installed pack. See
[web polygon mode](docs/web-polygon-mode.md) for coverage, assumptions and tests.
Outside the packs, connected exploration automatically uses the labelled 1 km
classifier on supported North American land. Missing terrain remains visible;
fine fuel/road spread is not available nationwide. Prepared polygon packs do
not depend on classifier artifacts. Offline, new placements outside packs remain
blocked; existing grid runs can continue locally.

The legacy `config/local_spread.json` supports startup preparation. Missing
configured resources are copied from `WILDFIRE_SOURCE_DATA_ROOT` (by default
the sibling `wildfiredetection/data` archive). Missing public NALCMS land cover
can be downloaded and verified; canopy and roads reuse retained archives.
Subsequent starts use this repository's own files. See [startup data preparation](docs/startup-data.md)
for storage, controls and measured checks. Its **Polygon spread · roads & fuel**
option is available only with the required expanding archive installed; it is
not a fallback for the prepared, bounded pilot regions.

Enable **Weather ML + polygon (experimental)** to combine trained 1 km cell
admission probabilities with native polygon travel and captured weather wind.
Placement uses live forecasts by default; an optional date selects historical
weather without a satellite archive. Weather is pinned for replay. See
[weather sources, model coupling and limitations](docs/weather-polygon.md).

The public training data is in `htn_training/`. For geospatial playback, set
`WILDFIRE_ASSET_ROOT` to an existing directory containing retained `data/` and
`artifacts/` resources, or restore them in this repository. The default
trusted model is the boreal recovered incident run, pass 2. Override it with
`WILDFIRE_RUN_MANIFEST` and `WILDFIRE_MODEL_PASS`.

A completed local `artifacts/public-csv/run_manifest.json` takes precedence
over that legacy default. Its frontier model runs directly from CSV-derived
terrain; missing cells remain explicitly unsupported. Restoring full ETOPO
blocks extends terrain coverage. The offline weather policy is never silently
substituted for the recursive map model.

Other options: `WILDFIRE_DATA_ROOT`, `WILDFIRE_LOCAL_CONFIG`,
`WILDFIRE_VEGETATION_MANIFEST`, and `WILDFIRE_ALLOWED_HOSTS`. Optional local
landscape/vegetation configurations use relative paths under `config/`.
Adjust these when relocating sources. Missing resources disable the affected
capability; they never produce fabricated predictions.

Live FIRMS reads `NASA_FIRMS_API_KEY` or `MAP_KEY` from the server environment,
falling back to this repository's ignored `config/.env`. Restart the server after
changing the key; the usual uvicorn command loads it automatically. Only the
FIRMS credential is read from that file, and process environment values take
precedence. The app does not read another application's environment file.
Historical observations and offline simulations do not require the live key.
See [provider contracts and configuration](docs/providers.md) for the exact
selection rules, expected source paths and injection interfaces. See
[limitations](docs/limitations.md) for what the CSV-only checkout can support.

## Train and evaluate from the public CSVs

These commands run from the repository root. Set `TRAINING_CSV_DIR` or use
`--dataset` to select a different release. Verification checks every file in
`SHA256SUMS`, the complete CSV inventory, schemas and selected sidecar joins.

```bash
PYTHONPATH=src OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  python -m wildfire_data.model.training.public_csv train \
  --dataset htn_training --output artifacts/public-csv

PYTHONPATH=src OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  python -m wildfire_data.model.training.public_csv evaluate \
  --dataset htn_training --run artifacts/public-csv/run_manifest.json

PYTHONPATH=src OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  python -m wildfire_data.model.training.public_csv predict \
  --dataset htn_training --run artifacts/public-csv/run_manifest.json \
  --split held_incident --output artifacts/held-incident-predictions.csv
```

`verify` validates and loads the release without fitting. Training refuses an
existing output directory. It saves a fixed protocol before fitting, independent
frontier/geometry/weather classifiers, calibration, compact terrain lookup,
held-out metrics, and a completion manifest with relative artifact paths and
checksums. Reload predictions must match before completion is published.
`predict` exports paired probabilities for the selected held-out observed rows;
it does not fetch weather for new map cells.

To verify the supplied release independently of a model:

```bash
PYTHONPATH=src python -m wildfire_data.model.training.public_csv verify \
  --dataset htn_training
```

The actual reconstruction fit used **20,940** training and **3,403** calibration
examples. See [measured model results](docs/model-results.md) and the local
`artifacts/public-csv/evaluation.json`. Model files and training data remain
ignored by Git; reproduce the run or transfer trusted artifacts separately.

## Verification

The separate [Offline Planning v1 workspace](docs/offline-planning-v1.md) provides
local-only ignition/wind scenarios, durable SQLite checkpoints, baseline/variant
comparisons and portable reports. It does not replace the research APIs or promote
an experimental classifier. Use its isolated runtime and bundled-app build path;
the uncalibrated engine is explicitly research-only.

For the CPU model/feature comparison runner and cached/offline input contracts,
see [lightweight experiments](docs/experiments.md). It compares compact trees,
linear models, geometry/context features and optional weather/vegetation without
automatically replacing the map model. The full training CSV release is required
to measure accuracy; metadata alone is not sufficient. The downloaded release
is now available locally and the [first measured experiment round](docs/experiment-round-01.md)
is complete, including accuracy, model-size and laptop latency comparisons.
The [second experiment round](docs/experiment-round-02.md) isolates feature effects
with time-ordered validation and tests a small cached moisture-data source pilot.
The current target is an 8–16 GB RAM laptop; phone work is deferred.
See [product direction](docs/product-direction.md) for the rural/offline planning
and responder-support goals, proposed pilot sequence, and the distinction
between exploratory scenarios and validated operational use.

```bash
PYTHONPATH=src OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  python -m unittest discover -s tests -v
node --test frontend/tests/*.test.js
```

Tests include the reference behavior contracts for grid identity, finite-fuel
spread, local road barriers, HTTP validation, live observations, and historical
comparison. Runtime preparation uses a single worker and bounded shared caches.
The verified checkpoint passes 181 Python tests, 14 frontend tests, and production
Chromium checks for playback races, historical replay, 128-frame history,
2,000-cell displays, keyboard focus and mobile layout. See
[verification instructions and fixture boundaries](docs/verification.md) to
repeat the browser checks. Real-source vegetation and polygon playback have an
additional [browser and API verification](docs/startup-data.md#verification).

This is a research preview. The coarse map transition uses no weather; local
fuel/road travel is an uncalibrated scenario. The offline weather classifier
is a distinct observed-row model. Historical weak labels and fine simulation
geometry do not establish operational forecasting accuracy.
