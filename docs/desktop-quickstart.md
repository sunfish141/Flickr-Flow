# Wildfire Atlas — complete desktop preview

This unsigned internal build provides one map-based workflow with automatic
simulation routing. It is a research tool, not an operational wildfire
forecast. The older Saved scenarios workspace is no longer shown.

Open `WildfireAtlas.exe` inside the complete `WildfireAtlas` distribution folder.
Keep `_internal` beside it. It opens its own window; Python, Node and a browser
installation are not required. Do not run as administrator. If security software
blocks the unsigned application, use your organization's review process; do not
disable security controls.

The full-region Windows distribution includes **the whole province of Alberta**
and **the whole state of Colorado**. No first-run download is needed. Select
either under **Installed regions**, zoom in, and place a fire in supported fuel.
The boundaries, 30 m historical land cover and complete regional road extracts
are bundled. The 100 m simulation mesh is built locally in small 3 km tiles as
needed; it is not a province-sized mesh kept in RAM. Online street-map tiles and
current satellite detections are separate and require a connection.

- **One map:** Alberta and Colorado are outlined and labelled. Their
  sidebar buttons move the view; they do not change simulators or reset a run.
  Placing an ignition automatically selects the installed pack at that location.
  When connected, locations outside installed packs use the existing **1 km
  research model** on supported North American land. The interface explicitly
  labels this change in resolution and shows missing-terrain warnings. Its
  polygons are actual grid-cell footprints, not fine-scale fire perimeters.
  It has no detailed local fuel/road barriers or live weather. Manual grid
  ignitions use scenario strength 1, not a measured intensity or probability.
- **Offline overview:** bundled Natural Earth land/lakes provide orientation even
  without online tiles. Local raster tiles show land cover throughout Alberta
  and Colorado, with roads at close zoom. Offline, new placements elsewhere explain that regional
  data is not installed. An already-started grid calculation can continue locally
  after connection loss. A scenario cannot mix engines or different packs; reset
  before switching. Online maps do not download additional detailed packs.
- **Bounded runs:** each simulation can use up to 128 local tiles (1,152 km²) and
  500,000 road-cut fuel patches, regardless of the size of the installed region.
  Reaching a workload limit gives an error and retains the previous frame;
  resolution is never silently reduced. Spread stops at installed coverage edges.
  Purple land cover is urban/unknown, not evidence of safety. First placement in
  a new location needs local tile preparation; no internet request is involved.
- **Automatic connectivity:** the map uses online OSM tiles when they work and
  falls back to local layers on failure, retrying after 30 seconds. Device link
  status alone is not proof a map provider is reachable. There is no online/offline
  switch. The optional basemap layer checkbox remains a display/privacy control.
- **Satellite snapshots:** Settings accepts a session-only NASA FIRMS key. Close-up
  views centred on an installed region use fuel patches; broader views use the
  labelled 1 km model. Fine ignition positions are
  hypothetical approximations from observed 1 km cell centres; unsupported points
  are counted and excluded. Historical mode, if an archive is installed, creates
  an ignition snapshot for fuel patches; the grid model retains daily comparisons.
  Missing archives stay unavailable; preparation and downloads are never automatic.

The current map simulation is temporary and ends on reload/shutdown. Reset before
starting a different region; navigating elsewhere does not discard results.
Derived simulation tiles are kept under the OS user-data directory, never in
the installation folder. They share the 20 GB managed-storage admission cap
with saved cases and the application. Nothing is automatically deleted.

Existing planner data stays under the same OS user-data directory, on Windows
normally `%LOCALAPPDATA%\WildfirePlanner`. No migration or deletion is required.
The merged engine is v5. Older engine results remain viewable/exportable; their
continuation is blocked instead of silently changing the recorded engine.
The legacy planner keeps its 96-hour scenario cap; web exploration can play
longer, which does not establish accuracy beyond the evaluated horizons.
Legacy planner and coarse-model APIs remain for development/backward compatibility,
not as additional normal-UI workspaces. Removing the tab does not delete old cases.
Do not run the old planner and new app against that directory simultaneously;
the exclusive lock prevents this. Logs rotate in that directory as `desktop.log`.
The old `WildfirePlanner` executable remains separate and unchanged.

This build has no auto-updater, accounts, public signing or notarization. Source
dates, unknown road attributes, unsupported urban cover, uncalibrated spread
rates and unmodeled general ember crossing remain limitations. Native packages
must be built/tested on each target OS before claiming support there. Qt and
third-party notices are included; public distribution needs a licensing review.

Developer launch: install `requirements-desktop-dev.txt` in an isolated environment,
build both frontends, then use `PYTHONPATH=src python -m wildfire_data.desktop.launcher`.
`--browser` opens the same full app in an installed browser; `--no-ui --port 8001`
runs only the combined local service for tests. Neither mode changes the old web
server's legacy API contracts. `scripts/build_desktop.py` builds the distribution
and regenerates the overview from the bundled pinned Natural Earth source.
`WILDFIRE_FORCE_OFFLINE=1` is a deployment/test override that blocks outbound
connections; ordinary users do not need to configure connectivity.

Full-region build inputs are prepared separately with
`scripts/prepare_regional_inputs.py`; original national archives stay outside
the distribution. `scripts/build_desktop.py` requires both complete regional
datasets. `--pilot-only` explicitly builds the old limited engineering variant,
which must not be described as province/state coverage.
