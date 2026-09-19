# Flickr-Flow · Wildfire Atlas

An incremental, standalone recreation of the React frontend, FastAPI backend,
and wildfire models. This repository runs with `src` alone on the Python
path; it does not import another application's source. Original spatial and
prediction algorithms are ported to preserve trusted-model compatibility.
Runtime readers live in `providers/`; bulk collection commands are excluded.

The runtime separates API routes, resource lifecycle, and portable
settings. Existing model and state contracts remain supported. The frontend
retains the map, placement, FIRMS, historical comparison, inspection, detailed
landscapes, and timeline controls. Timeline transitions and request cancellation
are separate tested modules; all three reconstruction milestones are implemented.

## Run

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
  python -m uvicorn wildfire_data.web.app:app --host 127.0.0.1 --port 8001
```

`node frontend/build.mjs` is equivalent to the build script when dependencies
are already installed. Compiled assets are included. Open localhost on port
8001; another application can continue using port 8000. A new checkout also
needs trusted model artifacts: use the training commands below with the supplied
CSV release. The server can serve the interface without a model, but reports
the model unavailable and disables coarse simulation until configured.

## Local resources

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

Live FIRMS needs `NASA_FIRMS_API_KEY` or `MAP_KEY` in the server environment.
This app deliberately does not read another application's environment file.
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

```bash
PYTHONPATH=src OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  python -m unittest discover -s tests -v
node --test frontend/tests/*.test.js
```

Tests include the reference behavior contracts for grid identity, finite-fuel
spread, local road barriers, HTTP validation, live observations, and historical
comparison. Runtime preparation uses a single worker and bounded shared caches.
The verified checkpoint passes 74 Python tests, 11 frontend tests, and production
Chromium checks for playback races, historical replay, 128-frame history,
2,000-cell displays, keyboard focus and mobile layout. See
[verification instructions and fixture boundaries](docs/verification.md) to
repeat the browser checks.

This is a research preview. The coarse map transition uses no weather; local
fuel/road travel is an uncalibrated scenario. The offline weather classifier
is a distinct observed-row model. Historical weak labels and fine simulation
geometry do not establish operational forecasting accuracy.
