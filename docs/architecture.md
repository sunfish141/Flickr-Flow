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
Before serving requests, `providers/startup_data.py` verifies local sources,
imports configured vegetation/roads, or downloads pinned public NALCMS when
missing. Preparation is serialized and uses atomic files and storage admission.
Runtime-only configs and portable vegetation manifests keep the copied sources
independent of the old repository. Inspector and landscape sampling share the
staged TIFF cache. See [startup data](startup-data.md).
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

`frontend/src/scenarioState.js` defines immutable timeline transitions. It keeps
128 complete frames, seeks by absolute model step (including historical two-step
days), and replays stored future frames before requesting new predictions.
Trimming display history preserves fuel and burned-cell state in retained frames.

`requestCoordinator.js` permits one scenario request at a time. Each request has
an AbortController and a unique ticket. Cancellation retires the ticket before
aborting; a late response, failure or cleanup cannot affect a replacement request.
`useScenario.js` connects this lifecycle to React, source selection and playback
timers. Pause, reset, seeking, source changes and tab hiding cancel pending work.
Hiding also cancels explicit seed/FIRMS loads and clears their loading indicator;
completed server preparation may remain cached for a later retry.

[Provider contracts](providers.md) describe injection signatures, ownership,
configuration precedence and source restoration. [Limitations](limitations.md)
distinguish CSV-supported inference from optional source capabilities.
