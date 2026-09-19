# Flickr-Flow · Wildfire Atlas

An incremental, standalone recreation of the React frontend, FastAPI backend,
and wildfire models. This repository runs with `src` alone on the Python
path; it does not import another application's source. Original spatial and
prediction algorithms are ported to preserve trusted-model compatibility.
Runtime readers live in `providers/`; bulk collection commands are excluded.

The first milestone separates API routes, resource lifecycle, and portable
settings. Existing model and state contracts remain supported. The frontend
retains the map, placement, FIRMS, historical comparison, inspection, detailed
landscapes, and timeline controls while its internals are rebuilt incrementally.

## Run

From this directory, install `requirements.lock` into a Python environment.
An existing compatible environment can also be used. Build frontend sources
with Node 22 or newer:

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
8001; the parent application can continue using port 8000.

## Local resources

The public training data is in `htn_training/`. For geospatial playback, set
`WILDFIRE_ASSET_ROOT` to an existing directory containing retained `data/` and
`artifacts/` resources, or restore them in this repository. The default
trusted model is the boreal recovered incident run, pass 2. Override it with
`WILDFIRE_RUN_MANIFEST` and `WILDFIRE_MODEL_PASS`.

Other options: `WILDFIRE_DATA_ROOT`, `WILDFIRE_LOCAL_CONFIG`,
`WILDFIRE_VEGETATION_MANIFEST`, and `WILDFIRE_ALLOWED_HOSTS`. Optional local
landscape/vegetation configurations use relative paths under `config/`.
Adjust these when relocating sources. Missing resources disable the affected
capability; they never produce fabricated predictions.

Live FIRMS needs `NASA_FIRMS_API_KEY` or `MAP_KEY` in the server environment.
This app deliberately does not read another application's environment file.
Historical observations and offline simulations do not require the live key.

## Verification

```bash
PYTHONPATH=src OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  python -m unittest discover -s tests -v
```

Tests include the reference behavior contracts for grid identity, finite-fuel
spread, local road barriers, HTTP validation, live observations, and historical
comparison. Runtime preparation uses a single worker and bounded shared caches.

This is a research preview. The coarse map transition uses no weather; local
fuel/road travel is an uncalibrated scenario. The offline weather classifier
is a distinct observed-row model. Historical weak labels and fine simulation
geometry do not establish operational forecasting accuracy.
