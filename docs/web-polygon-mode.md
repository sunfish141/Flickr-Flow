# Web polygon mode — 2026-09-19

The original React/FastAPI web application now uses the existing local travel
engine by default when the two verified planning packs are installed. This is
real fuel-patch geometry from the engine, not circles redrawn as polygons. The
packaged offline planner and its executable are unchanged by this web update.

## Use

Start the web service using [the web environment](web-model-recovery.md), then
open http://127.0.0.1:8000 and hard-refresh. There is no simulator selector;
Black Hawk and Hinton are outlined on one map. Region buttons navigate only.
Place a supported ignition inside a dashed pack boundary, or open
**Place by coordinates** and use the prefilled supported example. Advance with
**+12 h** or Play. **Fit fires** fits actual perimeters. Orange is modeled active
fire and gray is modeled burned area; 1 km counts remain summary statistics.

Map browsing remains unrestricted. Only the two 9 × 9 km packs (81 km² each)
support detailed fuel-patch scenarios. Inside-pack unsupported ignitions still
return an error, never silently substituting the classifier. Online placements
outside packs use the existing labelled 1 km research model automatically. Its
polygons are actual equal-area grid footprints, not fine-scale fire perimeters.
It lacks local road/fuel data and live weather; missing terrain is flagged.
Offline, new outside-pack placements are blocked. Reset before changing engines
or packs; navigating alone never erases results. Close-up FIRMS views centred on
a pack use fuel patches; broader views use the grid model. Map sessions remain
temporary. The removed Saved scenarios tab does not delete existing cases; legacy
planner APIs remain available. See [the desktop quickstart](desktop-quickstart.md).

## Configuration and limits

- `config/local_spread_prepared.json` pins the pack manifest hashes and policies.
  Both manifests and their assets are checked before enabling the regions.
- With the prepared config and pack index present, normal Settings automatically
  choose that config and default preparation to off. `WILDFIRE_LOCAL_CONFIG`
  remains an explicit override. A checkout without the packs retains the legacy
  configuration; it does not imply these packs have been distributed with Git.
- The computational mesh is 100 m, matching these prepared packs. Playback in
  this existing web API remains 12-hour steps, stopping at a pack boundary.
  Merged upstream playback can extend beyond 96 hours, but this does not extend
  scientific validation. The persisted planner keeps its separate 96-hour cap.
- Rates are uncalibrated sensitivity assumptions, not fitted fire behavior.
  This web configuration uses calm wind (east/north 0 m/s), a 120-minute burning
  duration and zero spotting. There is no live weather or general ember crossing.
  The coarse classifier's arbitrary intensity slider is hidden in polygon mode.
- NALCMS source years are 2021 in Colorado and 2020 in Alberta; roads are pinned
  to Overture release 2026-08-19.0 (not a survey date). These are historical
  inputs, not current fuel observations. Source metadata is exposed in config
  and the interface. Missing road attributes stay unknown. Urban mixtures,
  structures and unknown cover are unsupported, not evidence of safety.
- The old national/expanding configuration is preserved but its archives are
  absent in this preview. No national downloads or experimental classifier
  promotion were required for the prepared polygon workflow.

## Verification

The initial selector-based implementation passed 46 backend/settings/engine
tests, followed by 15 local-API and
settings tests including new prepared-config, exact-coverage and checksum-failure
checks. All 16 frontend unit tests passed.

The earlier `frontend/tests/web-polygon-browser.mjs` targeted that selector-based
interface against the real web service and
both packs: default engine, supported seed, 12-hour step, Polygon/MultiPolygon
payloads and canvas rendering without circle markers, deterministic duplicate
step, region switching, outside-pack rejection and no coarse API fallback.
`frontend/tests/map-navigation-browser.mjs` verifies unrestricted panning, US
overview and no ignition during dragging on three viewport sizes. Browser test
tiles are local fixtures; tests do not prefetch public OSM tiles. Reports and
screenshots are under `artifacts/web-polygons` and `artifacts/web-navigation`.

The maintained integrated-map acceptance is now
`frontend/tests/unified-map-browser.mjs`, invoked by the frozen desktop check.
It tests real pack routing, online outside-pack grid placement and playback,
missing terrain, grid polygons, no engine mixing, offline rejection and
automatic reconnection. See [desktop acceptance](desktop-acceptance.md).

Current merged backend regression: 277 tests and 54 subtests; frontend: 24 unit
tests. The Windows run excludes the Unix-only legacy startup-preparation test.
These are software functionality checks, not operational fire-spread validation.
