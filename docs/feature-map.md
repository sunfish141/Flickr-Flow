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

Live FIRMS automatically reads the server credential from this repository's
ignored `config/.env`, with process environment variables taking precedence.
The normal startup command enables satellite loading when a key is present;
restart after changing it. The key remains on the server and is never returned
by `/api/config`. See [credential configuration](providers.md#configuration-selection).

After public-CSV training, coarse placement works without another repository's
source code or raw archive. Weather inference is available as a batch command
over observed examples, not as map forecast weather. Historical/vegetation/fine
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
Each detailed scenario remains limited to 24 tiles (216 km²), 500 starting points
and 96 hours; the app does not build a state/province-sized mesh. Selection is
retained when switching to FIRMS, which uses only the visible map area in detailed
mode. Zoom in before loading satellite detections. Fixed pilot APIs remain
available, with pilot choices shown as a fallback if the expanding archive is
unavailable.

Playback stores the most recent 128 complete frames. Scrubbing pauses requests;
resuming traverses saved frames before extending the simulation. Pause, reset,
source changes and hiding the tab discard pending results without clearing the
last completed frame. Reset clears the scenario. Provider failures retain the
last completed frame; rate-limited FIRMS requests show a bounded retry countdown.
Historical comparison advances 24 hours per frame and stops at the final day.

The map retains full scenario state while grouping dense markers for display.
The searchable cell list pages through every cell, including observations whose
map layer is hidden. Keyboard inspection restores focus when closed. The layout
was checked at desktop, 390 px and 320 px widths; see [verification](verification.md).
