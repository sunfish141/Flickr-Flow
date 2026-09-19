# Reconstruction verification

Validated September 19, 2026 in this repository using Python 3.14.4, Node 24.18.1,
Playwright 1.62.0 and its Chromium browser. The production bundle was rebuilt from
the committed React sources.

| Check | Result |
| --- | --- |
| Python behavior/regression suite | 181 tests passed |
| Frontend API, timeline and cancellation unit tests | 14 tests passed |
| Production Chromium interactions | Passed |
| Automated axe WCAG A/AA checks | No violations in five tested states |
| Browser JavaScript errors / CSP violations | None |

The browser suite starts with real API placement and 12-hour predictions through
144 simulated hours using the newly fitted CSV frontier model. It checks
keyboard placement, inspector focus, pausing in flight and resuming, and then
uses explicit API fixtures to test 2,000-cell display pagination, escaped API
text, the rolling 128-frame window, reset during inference, an overlapping
canceled request and replacement seed, retaining explicit loads when hiding the tab, source
changes, provider failure and retry cooldown.

Historical fixtures verify fixed comparison bounds, complete state requests,
24-hour advancement, the observation inspector and layer control, replay without
additional inference requests, and automatic stopping at the final day.
Historical archive loading and two-step simulation also have Python API tests
against generated source archives. Browser fixtures do **not** establish that
the real retained archive or live NASA connection is available on this server.

Basemap tiles are stubbed before navigation. Initial request checks find only
the app origin and the expected tile-provider URL. Accessibility audits cover
desktop initial state, desktop inspector, help dialog, and an open inspector at
390 px and 320 px widths. They are automated checks, not a complete manual
assistive-technology assessment. Screenshots and JSON reports are generated
locally under `artifacts/web-react-preview/` and excluded from Git.

Handoff checks also confirmed the training CLI help, consistent dependencies
via `pip check` in the tested environment, and valid local documentation links.
An app started with explicitly missing model/data paths still served its UI and
assets, reported unavailable capabilities, and returned a sanitized 503 for
simulation. The four focused FIRMS tests passed after aligning the missing-key
message with environment-based configuration. This was not a fresh dependency
installation or a live NASA connectivity check.

The later vegetation/polygon restoration passed real-source API and Chromium
checks for canopy inspection, road geometry, perimeters, expansion and replay.
Startup also passed without the old repository or downloads after restoration.
See [startup data verification](startup-data.md#verification) for measurements.

Colorado and Alberta regional checks also passed real-source polygon seeds and
12-hour expansion outside the old Boulder/Edson pilots. Each grew from one tile
to two. Browser checks verified both views and coordinate examples; isolated
FIRMS probes verified viewport bounds and regional selection across live and
historical controls without contacting NASA. Reports and frames are under
`artifacts/regional-landscape/browser/`; see the
[regional measurements](startup-data.md#colorado-and-alberta-verification).

Live FIRMS was verified separately on September 19, 2026 after fixing startup to
read this repository's `config/.env`. Real requests to all three VIIRS feeds
returned 165 eligible detections in 50 cells for the Alberta query and 15 in nine
cells for the Colorado query. These counts describe the 3–24-hour observation
window at verification time; they are not forecasts and change over time.

Chromium also loaded real current observations through the visible-area FIRMS
button and initialized the expanding polygon scenario around an observed Alberta
cell, with no JavaScript errors. The smoke test intercepts only basemap images;
satellite responses come from NASA. Reports and screenshots are stored under
`artifacts/live-firms/`. Five settings tests cover local credentials, environment
precedence, absent keys, file isolation and keeping credentials out of API output.

## Repeat the checks

### Landscape preparation and memory-cache checks

The cache/geometry checkpoint passed 130 Python and 14 frontend tests. New checks
cover retained tile identity, corrupt/missing assets, cache eviction, startup
warmup, reuse across region switches, and expansion without rebuilding old tile
graphs. Joined tiles match a direct mesh with roads, holes and wind. Existing
corner-leakage, spotting, fuel-residence and replay tests still pass.

Browser regressions verify that explicit seed and FIRMS loads survive tab hiding,
a busy response retries the same body without another click, and source changes
still invalidate late responses. API unit tests verify canceling a pending retry,
the retry limit, and propagating real source failures. Regional and live NASA
browser checks pass with the optimized engine. Measured 12-hour Colorado and
Alberta demo active/burned areas match the previous checkpoint.

Local timings on this machine (single process; OS file cache not cleared):

| Measurement | Before | After |
| --- | --- | --- |
| Colorado placement; native tile on disk, graph not in memory | 4.45 s | 1.82 s |
| Alberta placement; native tile on disk, graph not in memory | 3.15 s | 1.31 s |
| Returning to prepared Colorado after another region; engine only | 4.48 s | 0.0014 s |
| Alberta expansion into one new retained tile; engine only | not compared | 1.46 s |
| Next Alberta step with both tile graphs prepared; engine only | not compared | 0.046 s |

Real FIRMS HTTP checks loaded 15 observations across nine Colorado landscape
tiles: initial preparation took 18.32 s, revisiting after loading Alberta took
0.20 s including the 3.32 MB response. A narrow Alberta request took 0.61 s,
then 0.009 s on revisit. Initial Colorado preparation included previously
unbuilt tiles and source reads. These measurements demonstrate reuse; they are
not a guarantee for uncached locations or network latency. Reports and frames
are retained locally under `artifacts/landscape-performance/`.

After restarting the main server and preparing 24 retained tile graphs during
startup, the first live nine-tile Colorado FIRMS request took 3.86 s; the repeat
took 0.21 s. Both returned the same seven mapped cells and 3.32 MB response.
Startup prepares tile graphs; fetching current FIRMS observations, joining
tiles, and serializing the response still contribute to the first request.
These timings are recorded in `after-startup-http.json` in the same directory.

### Expanded landscape capacity and burnout

The expanded-capacity checkpoint passed 137 Python and 14 frontend tests.
Regression checks cross the former 24-tile boundary, submit the larger state
again, and preserve/replay burned area. They also verify configurable sampler
eviction, rejection of over-budget state before source reads, cumulative patch
admission before mosaic assembly, and configuration/request-size validation.
Changing capacity preserves the existing source/physics identity. A two-patch
test verifies that ignitions at minutes 0 and 30 burn out separately at minutes
120 and 150, with active and burned patches coexisting in one summary cell.

A real retained Colorado landscape with 36 tiles (324 km²) and 357,580 patches
at 30 m resolution passed seed, 12-hour advancement and exact replay. This
exceeds both the former 24-tile and 250,000-patch limits. Source loading took
17.11 s and graph preparation 46.34 s; creating the first frame took 1.30 s.
With that model prepared, advancement took 0.050 s and replay 0.033 s, excluding
HTTP encoding and transfer. The frame serialized to 4.99 MB. Peak process RSS
was 1,273 MiB. Other checks were running concurrently, so these timings are
illustrative rather than isolated benchmarks. Burned area remained 299,700 m²,
matching the same ignition in the smaller regional check. The local report is
`artifacts/landscape-performance/larger-budget.json`.

The default now permits 128 tiles (1,152 km²) and 1.5 million patches; the real
benchmark above tests 36 tiles, not the full ceiling. Larger areas consume more
RAM and require preparation when not cached. Browser regional checks also
verify the burnout explanation and a deliberately overridden server limit
(64 tiles / 576 km²), ensuring the UI does not hard-code the default.

### Native water and vegetation-dependent burnout

The fuel/water checkpoint passed 149 Python and 14 frontend tests. New checks
cover raster rivers absent from the generalized map, diagonal water-corner
blocking, cleanup of submitted burned-water state, and water filtering in
placement plus live/historical FIRMS APIs. Sharing the water raster cache does
not make later-published vegetation eligible at an earlier fuel cutoff.
Fuel tests verify type and density effects, missing versus zero fuel, partial
coverage fallback, assignment to later ignitions, separate burnout clocks,
API state roundtrip and exact replay without fuel refill. Polygon checks verify
class-specific residence in both burnout and shared-gate travel.

Real retained-data checks reproduced the reported Athabasca River gap near
Fort McMurray: six sampled cells previously accepted by Natural Earth contained
11.1%–74.5% NALCMS water and are now rejected. Lake Athabasca remains blocked;
Dillon Reservoir and Grand Lake cells missed by the old map are now blocked.
Reports are in `artifacts/fuel-water/water-probe.json`. After opening the source
readers, individual sampled land-cover/fuel checks took about 13–43 ms, with
repeat reads cached in memory; reader opening is primed at normal startup.

The real-source Chromium check `tests/web/browser_fuel_water.py` verifies those
API placement rejections, shows the assigned duration in the inspector, advances
a Fort McMurray bank ignition through 48 hours and replays every frame exactly.
The six known river cells appear in neither active/burned state nor candidates.
The bank ignition received a 16.24-hour duration and became burned at the
24-hour frame. A separate Colorado forest sample received 48.17 hours. These
values illustrate the configured proxy, not observed fire durations.

The numeric area/timing reports above describe their earlier policy checkpoints.
Fuel-dependent durations intentionally change active-versus-burned area totals.
Repeat the new check against a server with the retained native sources:

```bash
python tests/web/browser_fuel_water.py http://127.0.0.1:8000
```

### Polygon playback without a duration cap

The unlimited-duration checkpoint passed 157 Python and 14 frontend tests.
New model checks keep an active front spreading beyond 96 hours, replay earlier
frames after advancement and cache eviction, and verify that placement traverses
only its requested horizon. Advancing a completed fire does not replenish fuel.
Fixed-pilot API checks reach 144 hours; expanding API checks continue after
burnout through large step indices. Historical polygon checks reach 120 hours
and still stop at the last observation date. Invalid calendar overflows fail
before source loading.

`tests/web/browser_unlimited_landscape.py` passed with real retained Colorado
roads and vegetation through 168 hours, crossing 96 hours with Play and checking
saved-frame playback plus exact API replay. At one week, the scenario covered
14 tiles with 2,040 active patches and 49.98 km² of burned area. Individual steps
took 0.63–12.11 seconds including browser interaction and tile expansion;
the fire continued spreading beyond 96 hours. No JavaScript errors occurred.
Only basemap images were stubbed.
Reports, the final frame and a screenshot are saved locally under
`artifacts/unlimited-landscape/browser/`. This verifies continued execution,
not long-horizon forecast accuracy. Tile/patch budgets, fixed evidence boundaries
and historical date availability still apply.

### Weather ML polygon integration

The weather integration passed 181 Python and 14 frontend tests. The shared
production-browser regression also passed playback/cancellation, historical
fixtures, mobile layout and automated accessibility checks.

The hybrid regression checks cover pinned weather and units, causal six-hour
features, explicit missing terrain, probability-based cell admission, native
road/water barriers, separate fuel clocks, identity and cache eviction. Manual
historical placement works without an archived FIRMS store and stops at weather
coverage; satellite routes also select the correct weather source. The existing
classifier was reused without retraining or changing its published scores.

The real-data Chromium run `tests/web/browser_weather_polygon.py` passed all four
combinations below using native landscapes, the trained weather artifact and
actual Open-Meteo responses. Only basemap images were stubbed. Saved-frame
playback issued no new inference requests, repeated API steps matched exactly,
and no JavaScript errors occurred.

| Region | Weather source | Simulated time | Tiles | Active patches | Burned area |
| --- | --- | --- | --- | --- | --- |
| Colorado | Captured forecast | 36 h | 3 | 180 | 116.62 ha |
| Colorado | Historical analysis, starting August 20 | 60 h | 4 | 809 | 480.30 ha |
| Alberta | Captured forecast | 36 h | 3 | 215 | 84.45 ha |
| Alberta | Historical analysis, starting August 20 | 60 h | 4 | 325 | 217.88 ha |

These are execution checks, not observed-perimeter accuracy measurements. The
historical cases use placed ignitions and real historical weather; this server
does not currently retain the historical FIRMS archive. Weather-model transfer
to synthetic fire features remains experimental. The CSV terrain provider can
return missing terrain outside its retained cells. Reports, frames and
screenshots are under `artifacts/weather-polygon/browser/`.
After restarting the main server, the saved 36-hour Colorado forecast scenario
also reproduced its complete frame exactly from the persisted weather snapshot.

### Commands

Install the Python dependencies from `requirements.lock` and run `npm ci` in
`frontend/`. Run from the repository root:

```bash
PYTHONPATH=src OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  python -m unittest discover -s tests
node --test frontend/tests/*.test.js
node frontend/build.mjs
```

For browser verification, install the test browser and its system dependencies:

```bash
python -m pip install playwright==1.62.0
python -m playwright install --with-deps chromium
PYTHONPATH=src OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  python -m uvicorn wildfire_data.web.app:app --host 127.0.0.1 --port 8001
```

Keep that server running and, in another terminal, run:

```bash
python tests/web/browser_app.py http://127.0.0.1:8001
python tests/web/browser_restored_landscape.py http://127.0.0.1:8001
python tests/web/browser_regional_landscape.py http://127.0.0.1:8001
python tests/web/browser_unlimited_landscape.py http://127.0.0.1:8001
python tests/web/browser_weather_polygon.py http://127.0.0.1:8001
```

The browser suite requires a ready model. Train the public-CSV model first using
the root README commands, or explicitly configure trusted compatible artifacts.
Startup now restores or prepares vegetation and road sources; the local copy
has passed real-data checks. Real historical comparisons still require their
retained source archive. Models and source data are ignored artifacts; syncing
code alone does not transfer them.

For an opt-in live NASA check, supply coordinates near **current** detections in
supported vegetation, with observations 3–24 hours old:

```bash
python tests/web/browser_live_firms.py http://127.0.0.1:8001 \
  --latitude 53.516113169352934 --longitude -115.54892759738583
```

Those example coordinates were used for the September 19 check. Choose another
current detection area if they are now empty. The live test requires a valid
server credential and provider connectivity; it is separate from fixture tests.
