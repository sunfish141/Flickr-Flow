# Complete desktop preview: local verification

Verified 2026-09-19 on the Windows 11 development machine with Python 3.12.8
bundled by PyInstaller. This is an **unsigned, research-only internal preview**,
not a clean-install certification or an operational wildfire validation.

## What is included

The native Wildfire Atlas window contains one integrated map. It bundles the
reference classifier, local polygon engine, full Alberta/Colorado datasets, React
interface, Python service and Qt browser runtime. Installed pack outlines remain
visible; ignition coordinates automatically select the installed fuel-patch
simulation, or the existing labelled 1 km research model outside installed regions when
connected. Grid results render as their actual equal-area cell polygons, not
circles or fine-scale fire perimeters. Offline, new outside-pack placements stay
blocked; an already-started grid run can continue using bundled inference.
There is no simulator selector, Saved scenarios tab or online/offline switch.
Existing saved cases and their two original 81 km² pilot packs remain intact
behind the legacy API. The normal map displays the full province/state outlines.
Offline land-cover tiles and close-up roads are served from bundled 30 m rasters
and road Parquet, not downloaded map tiles. The simulation mesh remains 100 m,
loaded in bounded 3 km tiles around an ignition. See `full-region-offline.md`.

A pinned, generalized Natural Earth overview renders without internet. Online
tiles are used automatically when available, with local fallback and retry on
failure. FIRMS requests remain explicit and require a session key. Offline
acceptance uses a forced-offline deployment override. No development `.env`,
training archives or user scenarios are distributed. The old planner executable
is unchanged.

Existing planner cases use the same OS user-data directory. The frozen evidence
below includes the online-routing fix and upstream integration through `bf81312`.
The merge advances the local engine from v3 to v5: old saved frames remain
viewable/exportable, but continuation is blocked rather than silently changing
their engine. See `upstream-integration-2026-09-19.md` for integration checks.
Earlier numerical compatibility checks against the web environment produced
identical classifier probabilities for 256 deterministic probes (absolute
tolerance 1e-12). This does not demonstrate predictive accuracy.

## Packaged results

| Check | Result |
| --- | --- |
| Installed distribution, full regions and legacy packs included | 1,691,288,512 bytes |
| Service ready in a fresh process | 19.97 seconds |
| Restart ready | 21.47 seconds |
| Full native-window check | 32.69 seconds, including rendering, screenshot delay and shutdown |
| Legacy 24-hour saved-case calculation | 19.38 seconds |
| Full-region 24-hour runs, including deterministic replay | 2.92–15.11 seconds across four locations |
| Peak summed app/browser process-tree RSS | 1,561,116,672 bytes |
| Offline browser external requests | 0 |

The packaged test exercised automatic polygon routing by coordinate entry and
actual map clicks in both full regions, missing-pack messages, navigation without
discarding results, offline overview rendering, automatic connection recovery,
and six viewport sizes from 390 to 1440 pixels wide. It additionally verified
online placement/playback outside packs, visible 1 km labels, polygon geometry
and retention of the grid result after disconnection. It also exercised legacy
coarse inference, saved-case export and interrupted-worker restart through APIs.
The Windows executable was also launched from an isolated temporary working
directory, with outbound access blocked. Both full regions served nonempty
local land-cover map tiles and completed 12-hour polygon simulations. Four
additional offline 24-hour runs checked northern/southeastern Alberta and
southwestern/eastern Colorado, all far from the old pilot squares. Replaying the
same recorded inputs reproduced identical frames. Placement inside the Alberta
extraction envelope but outside its actual western border was rejected.
Catalog, manifest and asset checksums are verified at startup. Coverage is now
the full province/state, not the old 81 km² engineering areas. Unsupported cover
is still not evidence of safety, and a single run remains workload-bounded.
The captured native screenshot was visually inspected. Its headless Qt run
reported graphics-context fallback messages but rendered the integrated map and
exited successfully. Memory is summed process RSS, so shared pages can be counted
more than once; it is not a measurement of the whole operating system.

The final backend regression run passed 289 tests and 54 subtests. It covers automatic
regional routing, synthetic FIRMS mapping, forced-offline enforcement and
absence of development dotenv discovery, equal-area grid footprints and missing
seed-terrain disclosure. All 25 frontend unit tests passed. New tests cover
regional clipping, offline PNGs, deterministic boundary termination, quota and
checksum failures, independent map-request capacity, and build-input archive
path/link/size rejection. The legacy
`test_startup_data.py` module is excluded on Windows because its preparation
code imports Unix-only `fcntl`; preparation stays disabled in the desktop.
`pip check` and `git diff --check` passed. Frozen packaging needed explicit
collection of rasterio serialization and SciPy's dynamically imported NumPy
compatibility modules, in addition to the trusted joblib estimator classes.

Evidence is generated under `artifacts/desktop-smoke/frozen/`, including
`acceptance.json`, screenshots and logs. The release packager rejects stale
acceptance evidence by checking a digest of every distribution file. The verified
bundle identity is
`b4d739c13d7cd1a3d552d69b0479b7b246f7f2301ccc8cf262c7d815c510e7fa`.

The first new frozen pass ran alongside the backend regression suite. Its
30.16-second service startup narrowly missed the 30-second target; this evidence
is retained under `frozen-concurrent-regression-20260920/`. It passed the memory,
24-hour-run and native-window-check targets. Independent clean-device cold-start
and reference 8 GB laptop measurements are still outstanding.

The final table above is a repeat without the regression suite running. It met
the service-startup, memory and calculation targets, but the conservative full
native-window check took 32.69 seconds rather than under 30. That check includes
the deliberate 2.5-second screenshot delay and shutdown; it does not isolate
first usable paint. We do not claim verified sub-30-second native-window startup.
Further startup measurement/tuning remains a performance gate, not a reason to
reduce map coverage or simulation resolution silently.

The latest UI-only refresh removes routine seed, step and reset banners from
the map. Actionable errors still appear; missing-terrain details, unsupported
observations and pack-boundary warnings remain in the sidebar. Frozen browser
checks verify hidden routine notices, visible errors and retained limitations.
Compiled assets were updated with a backup; no saved cases were changed.

The layout correction removes an excessive minimum map height and preserves
Leaflet's container classes when ignition placement is toggled. Losing those
classes previously allowed off-screen map content to expand the page. Tests
cover repeated placement toggles, visible desktop playback controls and absence
of horizontal overflow. These are browser viewport tests, not independent
Windows display-setting/device checks. The complete executable was rebuilt;
the prior application was closed by the user before replacement.

## Remaining gates

- Independent clean-install and real network-disconnection checks, including a
  reference 8 GB laptop. Browser online tiles were fixture-intercepted in tests.
- Real FIRMS integration with an authorized key; no live provider request was
  performed during this verification.
- macOS/Linux native builds and installation checks; Windows CI is configured
  but has not been executed on an automated runner.
- Licensing review, signing and distribution approval before public release.
- Independent fire-behavior validation and domain review before operational use.

Detailed fuel-patch data now covers Alberta and Colorado. Each run is limited to
128 tiles (1,152 km²) and 500,000 patches, rather than constructing a whole-region
mesh in memory. Internet connectivity does not download additional detailed
regions. Online exploration outside those regions uses the existing 1 km research model, not the detailed
engine. Its terrain coverage is incomplete and missing inputs use the model's
trained handling. No live weather, fine fuel cover or road barriers are supplied.
Current map sessions are temporary and end on reload/shutdown. Previously saved
cases remain in the unchanged user-data directory. See the
[desktop quickstart](desktop-quickstart.md) for use and limitations.
