# Offline Planning v1 — implementation and acceptance

This is an **unsigned, internal, research-only** planner. Its local travel engine is
uncalibrated. Scenarios are sensitivity experiments, not forecasts, probabilities,
resource-allocation advice or evidence of treatment effectiveness.

## Architecture

The new `wildfire_data.planning` entry point is separate from the legacy
`wildfire_data.web.app`. It never reads `.env`, loads ML classifiers, imports
collection/conversion tools, or prepares data on startup. Legacy model exports
are lazy-loaded so the shared local engine no longer imports pandas/sklearn.
The ML experiment environment and original data are unchanged.

The installed browser connects to `127.0.0.1` on an automatically selected port.
The service and calculation worker deny outbound socket connections and remote
DNS. Browser CSP permits same-origin resources only; maps contain local land
cover, water, road vectors, bounds and coordinates, with no tile service.

One spawned worker builds an immutable local travel model for one case. Numerical
threads are capped at two. An acknowledged frame is committed with its checkpoint
in one SQLite FULL-synchronous transaction. The worker waits for that acknowledgement
before computing the next output. Pausing invalidates the generation before
terminating the process. Late results cannot commit. Restart pauses unfinished
runs; continuation deterministically recomputes the travel graph, retaining the
original ignition, wind, origin time, policy and frames.

SQLite lives in the OS user-data directory (`platformdirs`, app `WildfirePlanner`),
not the browser or install directory. An OS file lock prevents two servers sharing
one database. User mutations use revision compare-and-swap plus request UUIDs.
Previously calculated or imported definitions are immutable: clone to experiment.
Input revisions and background checkpoint progression are deliberately separate.

Definitions pin the full pack-manifest digest and engine identity (planner/engine
versions, NumPy/Shapely/pyproj and GEOS/PROJ versions). Behavior changes require a
version bump. Changed/missing resources block continuation, while retained vector
snapshots and frames remain available for viewing and exporting. No legacy
classifier is promoted.

## Developer commands

Use Python 3.12 and Node 22 **on the build machine only**. The existing `.venv`
belongs to ML experiments. On Windows, use the explicit Python executable if the
system `python` shim points at an uninstalled version.

```powershell
python -m venv .venv-runtime
.venv-runtime\Scripts\python -m pip install -r requirements-planning-dev.lock
$env:PYTHONPATH = 'src'
cd frontend
npm ci
npm run build:planning
cd ..
.venv-runtime\Scripts\python -m wildfire_data.planning.launcher
```

POSIX equivalents use `.venv-runtime/bin/python` and `PYTHONPATH=src`. The launcher
accepts `--no-browser`, `--port`, `--data-dir` and `--packs-dir` for tests/development.
Never point two instances at the same data directory. Normal browser reloads are
safe; unsaved input edits are explicitly marked and trigger an unload warning.

Preparation runs separately:

```powershell
python -m venv .venv-prep
.venv-prep\Scripts\python -m pip install -r requirements-planning-prep.lock
$env:PYTHONPATH = 'src'
.venv-prep\Scripts\python scripts/prepare_planning_packs.py
```

This retains pinned CEC/NALCMS national ZIPs under `data/preparation-archives/`.
The finalized prepared packs live in `data/planning-packs-v1/`; earlier local
pre-release preparation output, if present, is not loaded or shipped.
The independently resumable road collector is `scripts/fetch_planning_roads.py`.
Overture is pinned to `2026-08-19.0` and uses an intersecting bounding-box query
(not a containment query that would omit crossing roads). Prepared packs are
100 m nearest-neighbor classifications in aligned 9 × 9 km ESRI:102008 rectangles.
No increased measurement precision is implied. Unknown classifications remain
gaps; missing width, surface and grade evidence remain unknown.

Source ZIPs and training archives are excluded from installed storage, never
silently replaced or deleted. Pack collection records actual retrieval times,
source/component dates, transformations, attribution and SHA-256 checksums.
If an upstream checksum changes, preparation stops rather than accepting it.

### Audited source snapshot change (2026-09-19)

The initial downloads correctly failed the old project's ZIP checksums. The
official current ZIPs were retained and their embedded TIFF XML/citation metadata
reviewed. They explicitly identify NALCMS Ed. 2.0 and CC BY 4.0. The v2 metadata
states Canada 2020 (some imagery from 2019/2021) and CONUS **2021**, unlike the
2019 CONUS data in version 1. The new preparer pins these audited ZIP hashes:

- Canada: `464471954603d57bc5c1498538a360cda81c838aa6f5aec8d10ef432d531416a`
- USA: `f8308b40a2b0ced8fda2759f5b4066c4e8d80bf23dcf90b99da16e8557fa1508`

The old `config/vegetation_inspector_sources.json` was not rewritten. New receipts
retain the old expected hashes and explicitly disavow equivalence with those
missing archives. Pack and engine digests prevent blending old/new scenarios.

## Runtime contracts

- `GET /api/config`, `GET /api/packs`, `GET /api/packs/{id}` expose the offline
  profile, verified coverage, capabilities, limitations, storage and local layers.
- `GET/POST /api/scenarios`, `GET/PUT /api/scenarios/{uuid}`, and
  `POST .../clone`, `POST .../run`, `POST .../pause` manage durable cases.
- `PUT .../playback` persists a saved playback hour; reopening is paused.
- `GET .../frames` retrieves committed results; `GET .../export?format=json|html|geojson`
  produces portable JSON, a standalone script-free HTML/SVG report, or WGS84 GeoJSON.
- `POST /api/scenarios/import` accepts `{request_id, document}`. Strict v1 JSON
  only; no ZIPs, pickle/joblib, credentials or executable models. Imports are
  unverified/view-only; clone to recompute with matching trusted installed resources.
- All mutations use UUID request IDs; all updates/actions require `expected_revision`.
  Stale clients receive HTTP 409; failed storage writes receive HTTP 507 and are not
  acknowledged as saved. Portable documents are limited to 8 MiB; exports that would
  exceed reimport capacity fail explicitly rather than silently truncating frames.

The managed budget is 20,000,000,000 bytes (decimal GB), including the entire frozen
installation, bundled packs, SQLite/WAL and backups. Writes reserve three times the
payload plus page/commit overhead, and require free-disk headroom. No eviction.
Development counts pack/UI files and user data, not the ML/preparation environments.
The UI labels its initial storage reading as such; fresh API configuration reports
current usage. User-chosen exports outside managed storage are not automatically
deleted or managed by the app.

The first database schema is v1. Unknown/future schemas are backed up using SQLite's
backup API then rejected, never guessed or mutated. A future migration must add a
transactional, tested upgrade path; it is not acceptable to delete the database.

## Builds and testing

```powershell
$env:PYTHONPATH = 'src;tests'
.venv-runtime\Scripts\python -m pytest tests/planning -q
.venv-runtime\Scripts\python scripts/smoke_planning.py
.venv-runtime\Scripts\python scripts/build_planning.py
.venv-runtime\Scripts\python scripts/smoke_planning.py --frozen
.venv-runtime\Scripts\python scripts/verify_planning_exports.py
.venv-runtime\Scripts\python scripts/package_planning_release.py
```

Browser tests use the locked `playwright-core` dependency; install its test browser
with `node frontend/node_modules/playwright-core/cli.js install chromium` (Linux CI
uses `--with-deps`). This browser is a build/test dependency, not shipped. End users
use their already-installed browser.

Release packaging checks that the frozen acceptance report matches a SHA-256
fingerprint of the exact application file tree. It creates local ZIPs and checksums
under `releases/offline-planning-v1/`, refuses to overwrite them, and never uploads
or publishes a release. Real browser JSON exports are separately round-tripped
through a fresh source API store under the same pinned runtime.

Upstream engine regression tests now also import landscape-preparation code. Run
`tests/model/test_local_spread.py` in the preparation/QA environment with `pytest`
and `pyarrow` installed; these are not field-runtime requirements.

`build_planning.py` gates packaging on both real verified pilot packs. PyInstaller
onedir output is under `dist/WildfirePlanner/`. Keep the whole directory together;
the executable opens the browser, and closing its console stops the service.
Builds run on their target OS, never cross-compiled. Do not run the planner as admin.

The manually triggered GitHub workflow targets Windows Server 2025 x64 (build
runner), macOS 15 Intel, macOS 14 ARM64, and Ubuntu 22.04/24.04 x64. Build runners
are **not** proof of Windows 11 or macOS 14 Intel field compatibility. Hands-on
target-device installation tests remain release gates. Configure a reviewed
immutable pack ZIP URL and SHA-256 via repository variables
`PLANNING_PACKS_URL` / `PLANNING_PACKS_SHA256`; no credentials belong in chat or packs.
CI extracts only bounded JSON entries and rejects traversal, duplicates and symlinks.

Acceptance goals are startup <30 seconds, default 24-hour run <120 seconds and
combined app/browser peak RAM <4 GB on a reference 8 GB laptop. Smoke reports sample
the service/worker/browser process-tree RSS; shared pages may be double-counted.
Reports distinguish source and frozen builds, record actual machine RAM, and do
not imply a clean 8 GB reference-device test. Numerical comparison tolerances are
1e-6 m² for areas and 1e-9 degrees for coordinates on matching engine dependencies.

## Release gates still requiring external evidence

Use the [acceptance record](offline-planning-acceptance.md) for what was actually
exercised locally and the [quickstart](offline-planning-quickstart.md) to try the pilot.
Do not infer macOS/Linux installation success from committed workflow YAML.
Before field-ready claims: run the OS matrix, clean-install with host networking
blocked, test read-only installations and representative laptops, audit source
licensing for redistribution, and obtain a domain reviewer. Public signing and
notarization are separate later gates. Independent spread validation, moisture
experiments and remote-community/European coverage audits remain post-v1 work.

Primary references: [CEC NALCMS](https://www.cec.org/north-american-environmental-atlas/land-cover-30m-2020/),
[Overture transportation](https://docs.overturemaps.org/guides/transportation/),
[Overture attribution](https://docs.overturemaps.org/attribution/),
[OSM tile policy](https://operations.osmfoundation.org/policies/tiles/),
[PyInstaller operating model](https://pyinstaller.org/en/stable/operating-mode.html),
[GitHub runner matrix](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
