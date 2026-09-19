# Complete desktop preview: local verification

Verified 2026-09-19 on the Windows 11 development machine with Python 3.12.8
bundled by PyInstaller. This is an **unsigned, research-only internal preview**,
not a clean-install certification or an operational wildfire validation.

## What is included

The native Wildfire Atlas window contains one integrated map. It bundles the
reference classifier, local polygon engine, two verified regional packs, React
interface, Python service and Qt browser runtime. Installed pack outlines remain
visible; ignition coordinates automatically select the installed fuel-patch
simulation, or the existing labelled 1 km research model outside packs when
connected. Grid results render as their actual equal-area cell polygons, not
circles or fine-scale fire perimeters. Offline, new outside-pack placements stay
blocked; an already-started grid run can continue using bundled inference.
There is no simulator selector, Saved scenarios tab or online/offline switch.
Existing saved cases remain intact behind the legacy API.

A pinned, generalized Natural Earth overview renders without internet. Online
tiles are used automatically when available, with local fallback and retry on
failure. FIRMS requests remain explicit and require a session key. Offline
acceptance uses a forced-offline deployment override. No development `.env`,
training archives or user scenarios are distributed. The old planner executable
is unchanged.

Existing planner cases use the same OS user-data directory. The frozen evidence
below includes the online-routing fix and upstream integration through `4cbd8e6`.
The merge advances the local engine from v3 to v5: old saved frames remain
viewable/exportable, but continuation is blocked rather than silently changing
their engine. See `upstream-integration-2026-09-19.md` for integration checks.
Earlier numerical compatibility checks against the web environment produced
identical classifier probabilities for 256 deterministic probes (absolute
tolerance 1e-12). This does not demonstrate predictive accuracy.

## Packaged results

| Check | Result |
| --- | --- |
| Installed distribution, both packs included | 961,648,343 bytes |
| Service ready in a fresh process | 5.80 seconds |
| Restart ready | 6.94 seconds |
| Full native-window check | 11.00 seconds, including rendering, screenshot delay and shutdown |
| Default 24-hour saved-case calculation | 6.08 seconds |
| Peak summed app/browser process-tree RSS | 1,489,854,464 bytes |
| Offline browser external requests | 0 |

The packaged test exercised automatic polygon routing by coordinate entry and
actual map clicks in both packs, missing-pack messages, navigation without
discarding results, offline overview rendering, automatic connection recovery,
and six viewport sizes from 390 to 1440 pixels wide. It additionally verified
online placement/playback outside packs, visible 1 km labels, polygon geometry
and retention of the grid result after disconnection. It also exercised legacy
coarse inference, saved-case export and interrupted-worker restart through APIs.
The captured native screenshot was visually inspected. Its headless Qt run
reported graphics-context fallback messages but rendered the integrated map and
exited successfully. Memory is summed process RSS, so shared pages can be counted
more than once; it is not a measurement of the whole operating system.

The broad backend regression run passed 277 tests and 54 subtests, including automatic
regional routing, synthetic FIRMS mapping, forced-offline enforcement and
absence of development dotenv discovery, equal-area grid footprints and missing
seed-terrain disclosure. All 24 frontend unit tests passed. The legacy
`test_startup_data.py` module is excluded on Windows because its preparation
code imports Unix-only `fcntl`; preparation stays disabled in the desktop.
`pip check` and `git diff --check` passed. Frozen packaging needed explicit
collection of rasterio serialization and SciPy's dynamically imported NumPy
compatibility modules, in addition to the trusted joblib estimator classes.

Evidence is generated under `artifacts/desktop-smoke/frozen/`, including
`acceptance.json`, screenshots and logs. The release packager rejects stale
acceptance evidence by checking a digest of every distribution file. The verified
bundle identity is
`1a215205dc438fc707d2597b5bcb10325403e72def6e83c0785a10c931b0f47e`.

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

Detailed fuel-patch simulation remains bounded to Black Hawk and Hinton;
internet connectivity does not download new detailed packs. Online exploration
outside those packs uses the existing 1 km research model, not the detailed
engine. Its terrain coverage is incomplete and missing inputs use the model's
trained handling. No live weather, fine fuel cover or road barriers are supplied.
Current map sessions are temporary and end on reload/shutdown. Previously saved
cases remain in the unchanged user-data directory. See the
[desktop quickstart](desktop-quickstart.md) for use and limitations.
