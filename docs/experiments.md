# Lightweight ML experiments and offline inputs

The first experiment matrix compares five CPU candidates across four default
feature profiles. Each profile selects its own candidate by mean development
average precision (the reported PR-AUC). Selection uses only three
`StratifiedGroupKFold` folds within declared training incidents. Imputation,
scaling, bins and all-missing feature removal are learned within each fold.
The separate calibration incidents are used only after model selection.

| Candidate | Configuration |
| --- | --- |
| `hgb_reference` | Existing 300 iterations, 7 leaves, minimum leaf 200, L2 30, learning rate 0.04 |
| `hgb_compact` | 100 iterations, 7 leaves, learning rate 0.06; other reference settings retained |
| `hgb_flexible` | 200 iterations, 15 leaves, minimum leaf 100, L2 10 |
| `logistic` | Median imputation with missingness flags, scaling, regularized logistic regression |
| `binned_logistic` | Median imputation with missingness flags, eight quantile bins, regularized logistic regression |

All candidates train sequentially, with two CPU threads by default. There are
60 development fits for the default matrix, followed by at most eight final
classifier fits and their calibrators. No GPU or new training dependency beyond
scikit-learn is required. Python 3.12 and the small experiment requirements are
separate from the full web/GIS reconstruction environment.

| Profile | Inputs | Availability |
| --- | ---: | --- |
| `frontier` | 13 | Local fire state plus terrain |
| `geometry` | 21 | Adds observed 5x5 fire geometry and recency |
| `context` | 28 | Adds concentration, outer-ring/older counts, brightness max-minus-mean, annual phase |
| `weather` | 34 | Existing weather, wind projection and six-hour history |
| `vegetation` (opt-in) | 43 | Geometry plus static cover and its quality fields |

The context transforms use past feature values and the prediction cutoff only.
Undefined ratios stay missing. No label, incident ID or region ID is a predictor.
Candidate-relative slope, directional sectors, growth trends and raw spectral
features require source evidence that this CSV release does not contain; those
are later experiments. Static vegetation is opt-in because its previous
experiment failed promotion gates and its revisions are retrospective. Roads
remain excluded because historically eligible support is zero.

## Run

The Git checkout includes metadata, but the eight CSV files are ignored. They
are now present locally under `htn_training/`; on another machine, supply
the complete original release (about 4.17 GB) using `--dataset`. Checksums,
manifest schemas, one-to-one example IDs and cohort identities are verified.
No datasets are downloaded by the runner.

The checked-in metadata requires LF line endings to retain its original hashes.
`.gitattributes` pins those files to LF on Windows. This workspace's metadata
was restored to its original checksummed bytes; no hash inventory was changed.

PowerShell, from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-experiments.txt
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m wildfire_data.model.training.experiments `
  --dataset D:\datasets\htn_training `
  --output artifacts/experiments/round-01 --threads 2
```

In this workspace Python 3.12 was found under the user's local Programs/Python
directory; `.venv` has already been created. The global `python` shim had no
selected pyenv version, so use `.venv\Scripts\python.exe` directly.

To restrict the first sweep to inputs computable without weather:

```powershell
.\.venv\Scripts\python.exe -m wildfire_data.model.training.experiments `
  --dataset D:\datasets\htn_training `
  --output artifacts/experiments/offline-01 `
  --profiles frontier geometry context --threads 2
```

Use `--profiles frontier geometry context weather vegetation` for the static
vegetation ablation. Offline experiments still verify the complete release;
this is a training integrity requirement, not an on-device data requirement.

Outputs include the frozen protocol, per-fold development results, selected
models, calibrated reference models, holdout metrics, a human-readable
`summary.md`, latency/model-size measurements, and a checksummed completion
manifest. Existing output directories are refused. The artifacts are research
bundles and are deliberately not loaded by the map's production model loader.

Final testing uses held incidents, held regions and later time only after all
architecture choices are saved. Thresholds are fixed in advance: 0.20 for
individual models, 0.15 for the existing 75/25 reference weather blend.
Paired confidence intervals resample whole incidents, not neighboring rows.
The evaluation includes geometry on the same weather-supported rows, so added
geometry is not mistaken for a weather improvement. Weather availability
exclusions and class prevalence are reported explicitly.

These existing holdouts are reused benchmarks. Do not select another model
from their results and describe them as a fresh independent test. New incidents
or a later season are needed for independent confirmation. Multi-step rollout
and independent perimeter labels remain separate validation tasks.

## Offline behavior contract

Prediction requires local inputs, not an internet connection flag. A valid
forecast downloaded earlier can be used while disconnected. Conversely, having
internet does not make missing, expired or spatially unsupported data usable.

| Data | Offline behavior | Connected behavior |
| --- | --- | --- |
| Model and transforms | Installed locally | Download versioned updates |
| Terrain, static cover, land/water | Read a previously downloaded regional pack; preserve missing coverage | Refresh or expand local packs |
| Fire state | Manual ignition or saved local scenario; cached detections retain their actual age | Fetch new detections and record availability |
| Weather | Use a cached issued forecast only inside its validity window and permitted issue age | Fetch a forecast, validate coverage and history, then cache |
| Basemap | Needs packaged/cached tiles or a tile-free map | May fetch map tiles |

`model/input_availability.py` implements the pure routing rule. It checks the
original scenario information cutoff, download and issue timestamps, forecast
validity across the entire requested 12-hour interval, completeness, and a
configurable maximum issue age (initial assumption: 24 hours). A later
simulation step never renews a forecast's age. Providers must additionally
validate spatial coverage, units and required current/history fields before
marking a cache entry complete. The weather model must explicitly have passed
issued-forecast validation; the current historical-analysis model does not.

This routing helper does not yet enable complete app offline mode. The current
React app still calls the local FastAPI server, optional basemap tiles are
network resources, and startup can download vegetation. A laptop deployment
with prepared local resources can disable preparation/downloads via
`WILDFIRE_PREPARE_DATA=0` and `WILDFIRE_DOWNLOAD_VEGETATION=0`; native phone
inference, regional pack distribution and offline UI still need implementation.
The map's 13-feature model and heuristic recursive renderer are unchanged.

The experiment report measures a complete weather outage using the separately
trained geometry model and a retrospective weather-if-present fallback policy.
The latter is an analysis of input availability, not evidence of forecast
accuracy or operational permission to use historical analysis in live inference.

## Resource and correctness checks

The runner measures uncompressed artifact size, warm filesystem load time,
1024-row inference median/p95 over 20 repetitions, training duration and exact
reload parity. Timings include the classifier and calibration but exclude
feature preparation, geometry, maps and network. They describe this computer,
not a phone. Peak RAM, battery and mobile latency are explicitly unmeasured.
Measure those before enforcing mobile product budgets; no size/speed guarantee
is inferred from these tests.

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m unittest tests.model.test_experiments tests.model.test_public_csv -v
```

Synthetic fixtures verify split isolation, train-only preprocessing, full
experiment output/reload, missing weather, cache expiration, future-data
rejection, and valid cached forecasts while disconnected. Their scores are
not wildfire accuracy results.

Implementation verification: 24 focused tests passed in the experiment
environment. The downloaded release has now been verified and the first real
experiment sweep completed across all five profiles. See the
[round-one results and next steps](experiment-round-01.md). Models and raw
reports are retained in `artifacts/experiments/round-01/`; no model was promoted
to the map, and phone performance measurements remain outstanding.

API references: [incident-grouped folds](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.StratifiedGroupKFold.html),
[quantile bins](https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.KBinsDiscretizer.html).

## Training-only ablations and moisture pilot

The current target is an 8–16 GB RAM laptop; phone work is deferred. The
[second measured round](experiment-round-02.md) compares calendar phase,
fire-pattern transforms, moisture, wind and vegetation using matched feature
ablations. It does not evaluate calibration or published test cohorts, save
models, tune thresholds or promote a model. The original comparison runner now
also excludes development labels whose end reaches the later-test boundary.

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m wildfire_data.model.training.ablations `
  --dataset htn_training --output artifacts/experiments/round-02-ablations --threads 2
```

Use a new output directory for another run. The defaults freeze two HGB
configurations, 12 feature sets, three incident-grouped folds and three
forward-time folds. Forward training requires completed labels plus a 12-hour
embargo and purges validation incidents. Raw AP and expected recall at a fixed
top-20%-row budget are diagnostics, not calibrated warnings or area guarantees.

The separate weather probe requests at most eight coordinates, 14 days per
coordinate, six pinned historical IFS fields and 4 MiB per response. It saves
raw JSON, provenance, feature coverage and hashes. It never edits the training
release or joins labels. Prediction-time features use only complete hourly
windows at/before the requested cutoff. Reanalysis remains retrospective.

```powershell
.\.venv\Scripts\python.exe -m wildfire_data.model.training.weather_history_probe `
  --latitude 26.32689 54.02461 57.60984 `
  --longitude -106.91208 -75.17444 -97.93549 `
  --start-date 2026-06-20 --end-date 2026-07-01 `
  --cutoff 2026-07-01T12:00:00Z `
  --output artifacts/experiments/weather-history-pilot-01
```

These outputs already exist locally. For an interrupted probe only, use the
same command with `--resume`; it verifies the unchanged protocol and cached
responses before requesting the remaining locations. Completed caches cannot
be overwritten. One transient network/5xx retry is allowed per request; rate
limit and permission failures are not retried.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.model.test_experiments `
  tests.model.test_public_csv tests.model.test_ablations tests.model.test_weather_history_probe
```

The added tests cover incident purging, label embargoes, train-only scope,
order-independent score ties, history cutoff semantics, missing hours, units,
cache resumption and preservation. Network requests are mocked in unit tests.
