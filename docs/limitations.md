# Research limitations and missing inputs

This is a research spread explorer. Its learned probabilities and simulated
intensity are not validated fire perimeters or operational forecasts. The app
is not intended for evacuation or emergency decisions.

## What the supplied release supports

The verified public release contains 500,364 candidate examples and 35,265
diagnostic positives outside candidate support. The reconstruction fits the
applicable observed frontier subset: 20,940 training rows and 3,403 separate
calibration rows. Diagnostics are excluded from fitting. See the actual held-out
scores and protocol in [model results](model-results.md).

The coarse map model advances in 12-hour steps with finite fuel, deterministic
draws and burned-cell masks. It uses no weather inputs. The separately fitted
offline weather blend predicts over observed dataset rows; it does not provide
weather forecasts for arbitrary map cells or future simulation steps.

The terrain lookup covers 250,215 retained candidate cells. Outside that lookup,
terrain stays explicitly missing. The model can process missing features; a
displayed prediction therefore does not establish local terrain coverage. The
packaged land/water mask and 1 km grid also cannot resolve fine barriers or a
precise fire perimeter.

The frontier weights are a new fit. Weather policy point estimates reproduce
the reference benchmark, but the reused incident/region/time holdouts and
satellite-dependent weak labels are not a new independent validation. Road
training was unsupported by the retained historically eligible evidence.

## Capabilities awaiting optional resources

The local repository now contains trained public-CSV models and restored
vegetation, polygon pilots and national road data. Repeat preparation passed
without the old repository or network. See [startup data](startup-data.md).
The following resources remain external to Git and are needed on other machines:

| Capability | Missing input |
| --- | --- |
| Broader terrain coverage | Compatible ETOPO blocks beyond the CSV lookup. |
| Historical comparisons | Coverage ledgers and normalized detections for all three retained VIIRS feeds. |
| Vegetation measurements | Restored locally. Startup can download public NALCMS; canopy requires retained MOD44B/store data. Quality thresholds still leave some cells without canopy estimates. |
| Fixed fuel/road scenarios | Restored locally. Compatible Edson/Boulder bundles or explicitly configured equivalents are required elsewhere. |
| Expanding fuel/road scenarios | Restored locally. Requires the completed road archive; NALCMS and its direct-access raster cache are prepared at startup. |
| Live observations | A server-side FIRMS credential and working provider connection; no live feed integration was claimed from browser fixtures. |

[Provider documentation](providers.md) identifies the concrete paths and
interfaces. Training sidecars cannot reconstruct detections, publication
history, road geometry or 30 m fuel rasters. Restoring these resources is
separate from syncing Git: training data and generated models are ignored.

Historical comparisons cover May 11–August 21, 2026, with the final UTC day
ending at the August 22 boundary in the release name. They compare observations
with a continuing simulation; later observations do not reseed each step.

Fine fuel/road playback is an uncalibrated scenario using explicit travel rates
and constant wind. Unknown road widths use a documented assumption where
applicable; unsupported urban mixtures and structures are not modeled. A current
retained landscape can support a labeled retrospective historical scenario,
but does not establish that its inputs were available during May–August.

## Verification and operation boundaries

The checkpoint passed 117 Python tests, 11 frontend tests and production-browser
checks. Real API/browser checks used the trained coarse model and restored
vegetation/road sources, including polygon expansion and replay. Large-display
and historical browser cases used explicit fixtures; engine edge cases also
have synthetic tests. Live NASA and real historical archive integration remain
separate from these checks.
Automated accessibility checks found no violations in the tested states;
manual assistive-technology testing remains separate. See [verification](verification.md).

Scenarios live in the tab's memory and clear on reload. Shared caches are bounded,
and the app uses admission locks for expensive work; this is a single-process
research runtime without accounts or durable scenario storage. Request and host
checks are not authentication. No public deployment was performed.

Coordinates and state go to the app server. The optional basemap, enabled by
default, sends tile requests to OpenStreetMap; live FIRMS sends the selected
bounds through the server to NASA. The app adds no analytics or browser storage.
Code licensing does not independently establish redistribution rights for each
external source dataset.
