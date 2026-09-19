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

After public-CSV training, coarse placement works without another repository's
source code or raw archive. Weather inference is available as a batch command
over observed examples, not as map forecast weather. Historical/vegetation/fine
capabilities report absent sources rather than synthesizing them from labels.

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
