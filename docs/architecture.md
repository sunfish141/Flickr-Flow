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

Detailed landscapes retain up to 48 verified tile samplers and 48 prepared tile
graphs (at most 250,000 patches), plus eight assembled models (at most 500,000
patch references). Sampler cache hits check source file size and modification/
change times; changed files are rehashed and decoded. Model keys use verified
source hashes within the current mesh/policy instance. Tile graphs retain
road-cut geometry and internal adjacency. Expansion reuses those graphs and
constructs only the gates across tile boundaries. Vectorized GEOS operations
preserve polygon geometry, holes, road cuts and shared-gate rules.

Before readiness, `expanding.prewarm_tiles` prepares regional example locations
and recently generated native tiles, up to 24 by default. Set it to zero to
disable warmup. This moves graph construction into startup for retained areas;
new areas still require preparation. The cache is bounded, rather than a mesh
of the entire state/province. Derived tile files persist on disk; prepared
graphs are rebuilt into memory after restart. Cache eviction does not delete
source files or change scenario state. Model v3 and expanding profile v2 require
a fresh landscape scenario after upgrading.
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
timers. Pause, reset, seeking and source changes cancel pending work. Hiding the
tab pauses playback and cancels an outstanding playback step, but lets explicit
seed/FIRMS loads finish under their original ticket. A canceled HTTP request
can still finish preparation on the server; subsequent requests reuse that work.
Admission-busy 503 responses include `Retry-After`. The browser retries those
under the same ticket, up to 60 times with waits capped at five seconds. Pause
also aborts the retry wait. Preparation failures without that header propagate
immediately; provider 429 cooldown behavior remains separate.

[Provider contracts](providers.md) describe injection signatures, ownership,
configuration precedence and source restoration. [Limitations](limitations.md)
distinguish CSV-supported inference from optional source capabilities.
