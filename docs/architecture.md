# Application boundaries

`web/app.py` assembles FastAPI, request limits and static assets. `settings.py`
reads explicit environment configuration. `runtime.py` owns models, readers,
locks and bounded caches; `routes.py` handles stateless scenario requests.
Injected resources avoid loading unrelated optional sources in tests.

`model/` preserves spatial, feature, observation-aging and finite-fuel contracts
while remaining independent of FastAPI. `model/training/public_csv.py` implements
a new training entry point over the public release, without collection jobs or
private original manifests. `model/estimators.py` holds importable calibrated
estimator types. Model startup verifies trusted local artifact hashes before
deserializing; HTTP clients cannot supply artifact paths.

`providers/` holds source readers and bounded local landscape preparation.
There are no bulk collection entry points or simulation-time road downloads.
The CSV-only terrain provider returns explicit missingness outside the retained
candidate-cell lookup; source rasters remain necessary for broader terrain,
vegetation, historical observations and fine road geometry.

Training uses exact example-ID joins and source-manifest incident assignments,
separate training/calibration incidents and the original later-time boundary.
The offline weather blend and coarse recursive map model are separate artifacts.
The runtime defaults to a completed local public-CSV run when present. Legacy
run manifests remain supported through explicit settings.

The browser owns completed frames and playback timing. Request bodies contain
the full versioned model state. The API does not maintain per-user sessions or
silently truncate source data to fit display limits.
