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

Detailed landscapes default to 128 verified tile samplers and 128 prepared tile
graphs (at most 1.5 million patches), plus eight assembled models (at most 3 million
patch references). `expanding.max_tiles` and `expanding.max_patches` configure
scenario capacity, validated before opening source archives. Cache ceilings grow
with those limits; allocation remains demand-driven. The request schema admits
at most 512 tile records; the configured tile budget is checked before loading
submitted state or new tiles. The cumulative patch budget is checked before
assembling a mosaic, including scenarios with many road fragments. Capacity is
exposed through `/api/config` and frame metadata; the frontend displays that value.
Capacity is not included in the physics identity, so raising a limit does not
require resetting otherwise compatible scenarios.
Sampler cache hits check source file size and modification/
change times; changed files are rehashed and decoded. Model keys use verified
source hashes within the current mesh/policy instance. Tile graphs retain
road-cut geometry and internal adjacency. Expansion reuses those graphs and
constructs only the gates across tile boundaries. Vectorized GEOS operations
preserve polygon geometry, holes, road cuts and shared-gate rules.

Prepared graphs also retain numeric patch centers, areas and distances to the
current evidence boundary. Both polygon engines traverse numeric coordinates;
expansion examines boundary patches instead of every previously burned patch.
Expansion finishes its graph passes before rendering one final perimeter.
Perimeter rendering caches the latest active and burned union for each 1 km
cell, keyed by the exact set of patch IDs. Completed cells reuse their shapes;
rewind and other ignition sets replace a cache entry when its membership changes.
The cache has at most two entries per cell in its owning bounded model cache.
Cell grouping changes polygon coordinate ordering and floating-point area
roundoff, without simplifying geometry or changing spread probabilities.

Graph construction uses prepared containment for vegetation squares without
road cuts. Fully contained squares need no polygon intersection. Cover edges,
holes and all road-cut squares retain exact clipping, including stable patch
IDs. These optimizations preserve model/profile identities and existing states.

Before readiness, `expanding.prewarm_tiles` prepares regional example locations
and recently generated native tiles, up to 24 by default. Set it to zero to
disable warmup. This moves graph construction into startup for retained areas;
new areas still require preparation. The cache is bounded, rather than a mesh
of the entire state/province. Derived tile files persist on disk; prepared
graphs are rebuilt into memory after restart. Cache eviction does not delete
source files or change scenario state. Model v5 and expanding profile v2 require
a fresh landscape scenario after upgrading. Class-specific polygon residence
times are included in the policy identity.

Polygon arrival searches resume Dijkstra only through the requested time,
retaining tentative future arrivals and the pending queue. Each model keeps
at most eight ignition-set searches in an LRU cache. Settled arrival times let
earlier frames replay exactly after advancement; eviction recomputes the same
search, and wind changes clear it. Expansion rebuilds the search over the enlarged
mosaic while reusing prepared tile graphs. There is no 96-hour cutoff or full
future search during placement. Normal playback can continue after natural
burnout, without replenishing fuel. Spatial budgets and historical date limits
remain in force. API input validates the next representable calendar timestamp
before loading sources, rather than imposing an arbitrary forecast horizon.

The CSV-only terrain provider returns explicit missingness outside the retained
candidate-cell lookup; source rasters remain necessary for broader terrain,
vegetation, historical observations and fine road geometry.

Training uses exact example-ID joins and source-manifest incident assignments,
separate training/calibration incidents and the original later-time boundary.
The offline weather blend and coarse recursive map model are separate artifacts.
An optional `WeatherLandscapes` resource loads the verified weather artifact for
expanding polygon playback. `HybridSearch` freezes fire proxies and ML admission
decisions per 12-hour window, then advances the native graph with weather wind.
Each model caches at most two such searches. Pinned weather snapshots persist
under the runtime data root, with 32 in memory and a 256 MB disk cap. Their
digests, model identity and origin bind replay; graph expansion recomputes from
the same initial ignitions. The standard engine retains its existing behavior.
See [weather coupling and source contracts](weather-polygon.md).
The runtime defaults to a completed local public-CSV run when present. Legacy
run manifests remain supported through explicit settings.

The browser owns completed frames and playback timing. Request bodies contain
the full versioned model state. The API does not maintain per-user sessions or
silently truncate source data to fit display limits.

Each polygon response point includes `cell_geometry`, the canonical 1 km square's
corners transformed from ESRI:102008 into WGS84. These are inspection footprints;
the detailed perimeters remain the fire geometry. Leaflet draws selectable square
outlines and a separate non-interactive selection highlight. Historical markers
stay above these hit targets. Polygon selection uses cell identity independently
of active/burned status, so the inspector survives burnout and rewind. The same
inspector uses existing per-cell fire/road fields and the cached vegetation API.

The coarse preview's `model/fuel.py` assigns vegetation-based durations while
leaving offline training transition classes unchanged. Fuel-aware active and
evidence subclasses carry duration, vegetation fraction and basis through
validated API state; source selection is frozen at origin and active durations
are never refilled during replay. The shared inspector sampler supplies cached
land-cover/canopy evidence. Its raw current land-cover reader also supplements
the coarse Natural Earth barrier with NALCMS water, independently of historical
fuel eligibility. Runtime wires and primes these readers before accepting
requests. See [fuel and water contracts](fuel-and-water.md).

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
