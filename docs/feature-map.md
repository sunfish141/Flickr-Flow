# Feature availability

| Capability | Required resources |
| --- | --- |
| Map, coordinate placement, inspection and timeline | Bundled React assets |
| Coarse learned simulation | Trusted frontier model; CSV terrain or ETOPO provider |
| Train/evaluate/predict offline models | Complete `htn_training/` public CSV release |
| Live satellite initialization | Server-side FIRMS key and provider connectivity |
| Historical daily comparisons | Retained complete FIRMS source archive |
| Vegetation inspector | Verified local vegetation stores/rasters |
| Fine fuel/road simulation | Native fuel bundles or NALCMS plus offline indexed roads |
| Weather ML polygon simulation | Verified public weather model, terrain provider and captured Open-Meteo weather |

Live FIRMS automatically reads the server credential from this repository's
ignored `config/.env`, with process environment variables taking precedence.
The normal startup command enables satellite loading when a key is present;
restart after changing it. The key remains on the server and is never returned
by `/api/config`. See [credential configuration](providers.md#configuration-selection).

After public-CSV training, coarse placement works without another repository's
source code or raw archive. Weather inference is available as a batch command
and in the optional experimental polygon hybrid. Historical/vegetation/fine
capabilities report absent sources rather than synthesizing them from labels.

Vegetation and polygon sources now prepare at startup, and their real archives
are restored locally. The inspector reports measured land cover and canopy
estimates with sufficient quality support. Simulation choices include **Polygon
spread · roads & fuel**, **Colorado · polygon spread** and **Alberta · polygon
spread**. The regional choices replace the small Boulder/Edson demos, frame the
whole state/province, and offer example ignitions near Black Hawk and Hinton.
They use the US/Canada source archive and prepare local tiles wherever a fire
is placed in supported vegetation. Polygons use native roads and fuel patches,
expand over local tiles and support timeline replay. See
[startup preparation and measured checks](startup-data.md).

Regional bounds are map presets, not administrative boundaries or fire barriers.
Each detailed scenario defaults to 128 tiles (1,152 km²), 1.5 million fuel patches,
and 500 starting points, with no simulation duration cap. Tile and patch budgets are configurable in
`config/local_spread.json`; the app displays the server's current area limit.
The app does not build a state/province-sized mesh. Selection is
retained when switching to FIRMS, which uses only the visible map area in detailed
mode. Zoom in before loading satellite detections. Fixed pilot APIs remain
available, with pilot choices shown as a fallback if the expanding archive is
unavailable.

**Weather ML + polygon** uses the trained weather policy to admit new 1 km cells
while native roads, water and fuel constrain their detailed spread. New fires
use a captured forecast; a historical placement date selects analysis without
requiring a satellite archive. Historical FIRMS also selects analysis. One
representative weather location applies to the scenario. Weather and model
identity are pinned for replay. Beyond a live forecast, the last conditions are
held constant with an explicit label; historical weather stops at its coverage
boundary. This coupling is experimental. See [weather polygon](weather-polygon.md).

Playback stores the most recent 128 complete frames. Scrubbing pauses requests;
resuming traverses saved frames before extending the simulation. Pause, reset,
and source changes discard pending results without clearing the last completed
frame. Hiding the tab pauses playback while explicit fire placement and FIRMS
loading continue. Busy landscape requests retry automatically; Pause cancels
the load and its retries. Reset clears the scenario. Provider failures retain the
last completed frame; rate-limited FIRMS requests show a bounded retry countdown.
Normal polygon playback advances 12 hours per frame for as long as requested,
including after the fire burns out; exhausted fuel does not regrow. Historical
comparison advances 24 hours per frame and stops at the final available day.
Fixed pilots still stop at the boundary of collected evidence. Start a new
polygon scenario after upgrading to the unlimited-duration engine.

Burnout follows each ignition's vegetation-based fuel estimate. The 1 km model
uses land-cover type, mapped vegetation fraction and quality-supported canopy
density; missing measurements use an explicit 24-hour fallback. Duration and
evidence basis appear in the active-cell inspector. Polygon patches use
fuel-specific durations of 60–240 minutes from their own ignition times.
A summary cell can contain both active and burned patches and contribute to
both counts. Historical daily frames do not reset these clocks. The Help dialog
explains the assumptions; burned fuel cannot reignite within the same scenario.
See [fuel duration and water barriers](fuel-and-water.md) for the exact rules.

The 1 km model now combines the generalized coast/lake geometry with retained
30 m land-cover water measurements. Whole water/mixed-shoreline cells are
excluded from placement, satellite initialization and spread, and diagonal
steps cannot cut between blocked river cells. Current retained water geography
also applies to historical scenarios; fuel evidence keeps its origin cutoff.

Road/fuel tiles and their prepared spread graphs stay in bounded memory caches.
Startup prepares the regional examples and recent retained tiles before serving
requests. Expanding fires reuse existing geometry and add only new tile graphs
and their connections. New, uncached areas can still take longer to prepare.
The default now preloads all retained roads within the Alberta and Colorado
preset bounds into RAM and warms up to 128 previously generated tiles. Server
configuration can select either region, adjust the memory budget, or disable
preloading. Readiness and fallback status appear in `/api/config`; see
[preload configuration](startup-data.md#landscape-memory-preparation).
Vegetation rasters retain their existing reader/block cache, while complete
province/state travel meshes remain too large to keep in memory. Exact spatial
queries and smaller caches of assembled scenarios reduce scattered-fire CPU
work and memory pressure without changing spread rules.
Large-fire steps reuse unchanged burned-cell perimeter shapes and numeric patch
geometry. Expansion renders once after all needed tiles have been added; it
checks the evidence boundary without revisiting every burned patch. Both the
standard and weather ML polygon modes use these optimizations. Road/water
barriers, fuel clocks and ML admission rules are unchanged. See the
[measured performance and regression checks](verification.md#large-polygon-performance).

Polygon fire cells are clickable 1 km inspection squares. Clicking one highlights
the canonical grid cell and opens the same inspector as the coarse model, with
active/burned area, mapped roads, coverage and vegetation evidence. The detailed
fire perimeter still shows the actual simulated footprint; a square need not be
fully burned. Selection follows the cell through burnout and timeline rewind.
The cell list provides the same highlight through keyboard controls. Squares
respect active/burned layer visibility, including cells containing both states.

The capacity target is about **300 affected 1 km cells**, counting each cell once.
A real Alberta weather/polygon run reached 305 cells with 50 tiles under the
existing 128-tile/1.5-million-patch limits; those defaults remain unchanged.
This is a tested scenario size, not a new 300-cell cutoff or a guarantee that
widely scattered ignitions fit the same tile budget. See
[capacity and inspection verification](verification.md#polygon-cell-inspection-and-300-cell-capacity).

The map retains full scenario state while grouping dense markers for display.
The searchable cell list pages through every cell, including observations whose
map layer is hidden. Keyboard inspection restores focus when closed. The layout
was checked at desktop, 390 px and 320 px widths; see [verification](verification.md).
