# Providers and configuration

The app prepares vegetation/road sources on startup and reads local sources and
transient live observations. It does not collect training archives or infer
missing source geometry from labels. See [startup preparation](startup-data.md).
`create_app()` in [web/app.py](../src/wildfire_data/web/app.py) accepts a model
and providers for tests and integrations. The model layer does not import the
web layer.

## Configuration selection

Set variables in the environment of the server process before starting it.
The live FIRMS credential also loads automatically from this repository's
ignored `config/.env` when neither credential environment variable is nonempty.
Only `NASA_FIRMS_API_KEY` and `MAP_KEY` are read from that file; no other settings
are imported, no environment variables are modified, and no parent/sibling
environment files are searched. Restart after changing credentials. Relative
environment paths are interpreted from the process working directory; run from
the repository root when using the paths below.

| Setting | Selection and effect |
| --- | --- |
| `WILDFIRE_RUN_MANIFEST` | Explicit trusted run. Otherwise a local `artifacts/public-csv/run_manifest.json` takes precedence; the fallback is `artifacts/incident-two-pass-recovered-20260907-boreal/run_manifest.json` under the asset root. |
| `WILDFIRE_MODEL_PASS` | Legacy run pass, default `pass_2`. Public-CSV runs select their frontier artifact directly. |
| `WILDFIRE_ASSET_ROOT` | Base for the legacy run and default `data/`. Defaults to the repository, except when its parent contains a `data/` directory. Set this explicitly when sharing archives. |
| `WILDFIRE_DATA_ROOT` | Overrides the terrain/historical source root, default `<asset-root>/data`. |
| `WILDFIRE_SOURCE_DATA_ROOT` | Retained archive for missing vegetation/road imports, default `../wildfiredetection/data` beside this repo. Completed local copies no longer depend on it. |
| `WILDFIRE_PREPARE_DATA` | Default `1`; set `0` to use directly configured sources without startup preparation. |
| `WILDFIRE_DOWNLOAD_VEGETATION` | Default `1`; set `0` to disable public NALCMS downloads while allowing local imports. |
| `WILDFIRE_LOCAL_CONFIG` | Local landscape configuration, default `config/local_spread.json` under the repository. Paths inside this JSON resolve relative to that JSON file. |
| `WILDFIRE_VEGETATION_MANIFEST` | Explicit vegetation feature-store manifest. Without it, `config/vegetation_inspector.json` supplies the store reference and expected digest. National land-cover fallback still uses the source configuration named in that JSON. |
| `WILDFIRE_ALLOWED_HOSTS` | Comma-separated accepted hostnames, default `localhost,127.0.0.1`. |
| `NASA_FIRMS_API_KEY`, `MAP_KEY` | Server-side live FIRMS credential. Selection order: environment `NASA_FIRMS_API_KEY`, environment `MAP_KEY`, repository `config/.env` `NASA_FIRMS_API_KEY`, repository `config/.env` `MAP_KEY`; first nonempty value wins. Neither is needed for retained historical data. |
| `TRAINING_CSV_DIR` | Training CLI dataset default, otherwise `htn_training`. CLI `--dataset` overrides it. This is not a runtime map source. |

For example, explicitly select a locally trained run and local source root:

```bash
export WILDFIRE_RUN_MANIFEST=artifacts/public-csv/run_manifest.json
export WILDFIRE_DATA_ROOT=data
PYTHONPATH=src OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  python -m uvicorn wildfire_data.web.app:app --host 127.0.0.1 --port 8000
```

Startup preparation restores archived references under the selected data root
and generates runtime configurations there. With `WILDFIRE_PREPARE_DATA=0`,
changing the asset/data root does not rewrite paths inside vegetation or
landscape configuration files; restore the referenced files or update their
paths and hashes explicitly. The existing
hashes identify specific retained resources; unrelated replacements need their
own provenance and compatible manifests.

## Injection contracts

The optional expanding weather hybrid selects `weather.joblib` from the same
verified public run as the coarse model and uses its training-only observation
calibration. The normal startup exposes it through `/api/config` under
`local_spread.weather_ml`. It does not fetch weather at startup: opting in at
placement or FIRMS loading captures the relevant forecast/analysis once.
Snapshots remain under `<data-root>/runtime/scenario-weather/` for replay.
Missing weather fails explicitly. See [source, cache and time contracts](weather-polygon.md).

| `create_app` argument | Contract |
| --- | --- |
| `model` | An `IncidentTransitionModel`, normally returned by `model.loading.load_pass_model`. Startup wraps it with `FireSpreadModel.from_incident_model`; ordered feature columns, calibration and transition behavior must match. |
| `terrain_provider` | Callable `(cell_id) -> dict` with canonical cell ID, terrain values and missingness. Valid values include elevation, slope, aspect sine/cosine, `terrain_valid` and `terrain_aspect_defined`. `terrain_coverage_status='sampled'` distinguishes coverage from missing inputs in API reporting. |
| `landscape` | Land/water eligibility and transition barrier interface consumed by `FireSpreadModel`. Defaults to the packaged `model.features.landscape.Landscape`. |
| `firms_loader` | Callable `(api_key, bounds, *, now) -> (RecursiveFireState, metadata)`, where bounds is `(west, south, east, north)` and `now` is timezone-aware. Follow the metadata fields returned by `web.live_firms.aggregate_current_firms`. Raise `LiveFirmsError` with a credential-free message on failure. |
| `historical_store` | Has `available` and `load(day, bounds) -> (normalized_rows, display)`. Rows use the live aggregator's detection schema. Display includes date, bounds, detection/cell counts and historical points. The built-in `HistoricalFirmsStore` is the reference. |
| `vegetation_sampler` | Has `sample_cell(cell_id, *, cutoff_at, simulation_at)` and a `sources` mapping. Returns the vegetation feature/missingness/lineage schema consumed by `web.vegetation.vegetation_summary`. The scenario origin remains the evidence cutoff during playback. |

Supplying `model` without explicit `settings` disables automatic preparation;
without `local_config` it also skips default optional source loading. Explicitly
supplied providers still apply. Supplying `settings` enables default loading for
providers not injected; its `prepare_data` field controls preparation. Injected
vegetation samplers remain caller-owned;
the runtime closes its own vegetation and landscape resources on shutdown.

Terrain/inference, live FIRMS, historical reads and vegetation sampling have
separate admission locks. Providers should be called through the runtime for
HTTP use; direct callers must protect shared readers and caches themselves.
See [API test examples](../tests/web/test_web_app.py) and
[historical provider tests](../tests/web/test_historical_firms.py).

## Expected local sources

| Source | Expected files and failure behavior |
| --- | --- |
| Public-CSV model | A completed run manifest and its adjacent checksummed `frontier.joblib`, `weather.joblib`, `terrain.csv`, protocol and evaluation files. Startup verifies the selected artifacts; it never trains or substitutes random predictions. Joblib files must come from a trusted source. |
| CSV terrain | Generated lookup for 250,215 retained candidate cells. Chosen for a public-CSV run when `<data-root>/static/etopo-2022-15s` is absent. Unknown cells return missing terrain flags, not invented elevations. |
| ETOPO terrain | Compatible compact `.npz` blocks in `<data-root>/static/etopo-2022-15s`. The directory's existence selects this provider even if empty; restore the actual blocks to gain coverage. Missing cells remain missing. |
| Historical FIRMS | Coverage ledgers in `<data-root>/manifests/coverage/` and referenced gzip JSONL files in `<data-root>/normalized/fire-detections/acq-date=YYYY-MM-DD/`. All three VIIRS feeds require complete or explicit empty coverage for each requested day; payload hashes are checked. Coverage is snapshotted at startup, so restart after restoration. |
| Vegetation | Feature-store manifest/assets named in `config/vegetation_inspector.json`; NALCMS archive paths, members and digests in `config/vegetation_inspector_sources.json`. Missing evidence returns unavailable values, never zero cover as a substitute. |
| Fixed fine regions | Verified native fuel/road bundles named by each region in `config/local_spread.json`, currently the Edson and Boulder pilot manifests. Fixed routes remain available; their menu choices are a fallback when regional presets are unavailable. Missing bundles are omitted. |
| Expanding landscape | Indexed road archive and partitions, NALCMS assets and compatible runtime raster cache named in the `expanding` configuration. The configured road archive is `data/raw/overture-road-archive/us-ca-2026-08-19.0/manifest.json`. Colorado and Alberta views come from `expanding.presets` and use on-demand tiles from the national sources. Missing sources leave this capability and its presets unavailable. |

`/api/config` reports configured capabilities. Historical `available` means
coverage entries exist; it does not guarantee every requested date is complete.
Live `firms_configured` means a credential or injected loader exists; it is not a
connectivity check. Each actual request still validates its inputs and sources.
