# Vegetation and polygon-spread startup

The server now prepares vegetation and road resources before accepting requests:

```bash
source .venv/bin/activate
PYTHONPATH=src OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  python -m uvicorn wildfire_data.web.app:app --host 127.0.0.1 --port 8000
```

First-startup progress appears in the server log. This machine imported **89
verified files** from the retained archives, with no downloads. The copied
runtime data and initial derived tiles occupy approximately **17.18 GB**, under
this repository's ignored `data/` directory. Repeat preparation passed with the
old repository and downloads disabled: **zero imports and zero downloads**.

## Preparation and configuration

Startup first verifies local files, then imports missing configured resources
from `WILDFIRE_SOURCE_DATA_ROOT`, defaulting to the sibling
`../wildfiredetection/data`. Only vegetation stores/rasters, the direct-access
land-cover cache, polygon pilots and the completed road archive are imported.
Files are independent copies; no old application code or environment is loaded.

If NALCMS land-cover archives are missing, startup downloads the pinned public
CEC ZIPs, verifies their provider hashes and stages selected TIFF members.
Each download is bounded to 4 GB and subject to the storage budget. Actual
capture times are recorded; a new download is not backdated to the original
archive. Land cover can serve without canopy data. Canopy density requires the
retained MOD44B rasters and feature store; authenticated NASA collection is not
performed automatically, and missing density is never invented.

The canopy store receives a portable manifest with local relative asset paths;
the original manifest/database remain intact. All road partitions, coverage and
manifest hashes must verify. On another machine, supply the retained road
archive through `WILDFIRE_SOURCE_DATA_ROOT`; startup does not recollect the
national road network. Simulation-time tile preparation uses only local roads.

Runtime configurations are generated under `data/runtime/`; repository configs
remain unchanged. Both the inspector and polygon engine use staged land-cover
TIFFs. Atomic copies/downloads preserve completed work on retry, and a process
lock serializes preparation. Corrupt existing files are reported and retained
for inspection. The storage policy permits 37 GB total under `data/`, with
19 GB for static sources. Preparation never starts model training.

Failed optional preparation retains the coarse model and reports capability
errors through `/api/config` and the interface. Fix the source and restart to
retry. Set `WILDFIRE_PREPARE_DATA=0` to use directly configured sources without
preparation, or `WILDFIRE_DOWNLOAD_VEGETATION=0` to permit imports but prohibit
public downloads. Explicit provider/model injection remains isolated from
automatic preparation unless settings enable it.

## Restored controls and measurements

Choose **Colorado · polygon spread** or **Alberta · polygon spread** in Simulation
to view the full state/province. Both use expanding 30 m fuel-patch playback from
the national archives, replacing the small Boulder/Edson demo choices. Zoom in
to place a fire anywhere with supported vegetation, or open **Place by
coordinates** to use the region's example near Black Hawk or Hinton. The general
**Polygon spread · roads & fuel** mode remains available for other locations.
Place a fire in supported vegetation, then use the usual step/playback controls.
Roads and active/burned polygons appear on the map. Default limits are 128 tiles
(1,152 km²), 1.5 million fuel patches and 500 seeds. Polygon playback has no
duration cap; historical comparisons still stop at the final available date.
Known road surface classes with missing widths
use the configured 6 m assumption; unknown surfaces remain unsupported. This is
an uncalibrated scenario with constant wind.

Fuel-specific polygon burning durations and the shared native water mask are
described in [fuel duration and water barriers](fuel-and-water.md). The coarse
model also reads these cached vegetation sources to assign per-cell fuel clocks.

`expanding.presets` in `config/local_spread.json` defines the regional map views
and example coordinates. `/api/config` exposes those presets only when the
expanding archive is available. The bounds are approximate view extents; they
do not restrict ignitions or stop spread at political borders. Each scenario
prepares nearby tiles on demand, rather than loading the entire region. FIRMS
retains the regional selection and uses visible map bounds; zoom in around a
fire before loading. Fixed pilot routes and bundles remain usable; their menu
choices are the fallback when the national archive is unavailable.

The cell inspector reports land cover and quality-supported canopy measurements.
The tested Edson cell has needleleaf forest and effectively 100% mapped vegetated
land, but only about 9.6% usable canopy coverage, below the 25% threshold. Boulder
and Toronto test cells returned canopy density around 57.2% and 70.5%.
Unavailable measurements remain distinct from zero vegetation.

## Verification

The **157-test Python suite** covers integrity, atomic import, repeat startup,
download failure and capture times, portable canopy loading, road barriers,
tile seams, polygon replay, vegetation quality and API behavior. Download
transport used fixtures; the real restoration reused archives without downloads.

Real API checks passed vegetation inspection, fixed Edson polygons and expanding
polygons. The expanding scenario grew from one tile to two at 12 hours, with
36 initial road features. Replay returned identical frames. Fixed and expanding
fire polygons had **0 m² overlap** with mapped road surfaces under the configured
width policy. Warm preparation succeeded without the source repo or network.

Real-data Chromium checks passed the canopy/land-cover inspector, polygon
rendering, tile expansion, saved-frame replay and 390 px layout without browser
errors. Repeat against the running app:

```bash
python tests/web/browser_restored_landscape.py http://127.0.0.1:8000
```

Install Playwright/Chromium as described in [verification](verification.md).
Local reports/screenshots are under `artifacts/restored-landscape/`.
Source data and models remain excluded from Git; syncing code does not transfer
the 17 GB runtime archive.

## Landscape memory preparation

Startup also warms the regional examples and recently generated native landscape
tiles, controlled by `expanding.prewarm_tiles` in `config/local_spread.json`
(default 24; zero disables it). Readiness waits for this work. Logs report the
warmup and the number of prepared tile graphs. Subsequent placement and satellite
initialization reuse those road-cut fuel patches and connections directly.

Verified tile samplers and prepared tile graphs are retained in memory, with
128-entry limits and a 1.5-million-patch graph budget by default. Eight assembled
models can retain at most 3 million patch references. Expansion reuses existing tile graphs
and computes only cross-tile connections. Source changes invalidate cache hits;
unknown cover and road barriers retain their original semantics. Native tile
files persist; in-memory graphs rebuild after restart. New areas still require
tile extraction and preparation, so startup does not promise instant access to
every location in Colorado and Alberta.

`expanding.max_tiles` and `expanding.max_patches` control scenario capacity;
restart after editing them. Sampler/tile cache capacity follows the tile limit
(with a minimum of 48 entries); graph caches follow the patch limit, and assembled
models share a budget twice that size. These are ceilings, not startup allocations.
The default tile limit is over five times the former 216 km² limit. Supported
configuration ranges are 1–512 tiles and 1–5,000,000 patches. Larger values need
more RAM and preparation time; complex road cuts can reach the patch limit
before the area limit. Keep both budgets in proportion to available resources.
Requests fail explicitly on either limit and preserve the last completed frame.
Changing capacity alone does not invalidate the scenario's source/physics identity.

Explicit FIRMS loads continue when switching browser tabs. If another request
is finishing preparation, the same browser request waits and retries; Pause
cancels that wait. Invalid sources still return a failure without automatic
retry. See [architecture](architecture.md) for cache and request limits.

## Colorado and Alberta verification

Both regional examples were verified outside the original pilot bounds using
real retained fuel/road data. Each advanced from one 3 km tile to two at 12 hours:

| Region / example | Coordinates (latitude, longitude) | Initial road features | Burned area at 12 hours |
| --- | --- | --- | --- |
| Colorado / near Black Hawk | 39.83, -105.54 | 149 | 29.97 ha |
| Alberta / near Hinton | 53.39, -117.64 | 443 | 16.14 ha |

These are scenario outputs, not observed fire measurements. Chromium verified
both regional selections, example coordinates, road/perimeter rendering,
12-hour progression, reset when changing regions, and 390 px layout. Separate
intercepted FIRMS requests checked that the map frames the full region, regional
selection persists across source tabs, and live/historical requests use the
expanding endpoint and visible bounds. Those control checks do not contact NASA.

```bash
python tests/web/browser_regional_landscape.py http://127.0.0.1:8000
```

Reports, real API frames and screenshots are written under
`artifacts/regional-landscape/browser/` and excluded from Git.
