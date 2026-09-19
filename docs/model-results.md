# CSV model reconstruction results

Completed September 19, 2026. These are measured results from the rebuilt
training command, using the full verified 500,364-row public release.

Training: 20,940 observed frontier rows. Separate calibration:
3,403 rows. Terrain lookup: 250,215 cells.

| Cohort | Model | Rows | PR-AUC | ROC-AUC | Brier | ECE | Precision | Recall |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| held_incident | frontier | 9,177 | 0.679282 | 0.728420 | 0.208893 | 0.056247 | 0.486839 | 0.960088 |
| held_incident | weather | 9,177 | 0.738366 | 0.794412 | 0.187283 | 0.052806 | 0.517508 | 0.966210 |
| held_region | frontier | 12,318 | 0.601746 | 0.720335 | 0.198926 | 0.051281 | 0.459805 | 0.790547 |
| held_region | weather | 12,318 | 0.649548 | 0.773393 | 0.184695 | 0.049346 | 0.459162 | 0.905466 |
| later_time | frontier | 37,743 | 0.468882 | 0.699256 | 0.170187 | 0.053454 | 0.337028 | 0.739226 |
| later_time | weather | 37,743 | 0.509426 | 0.750390 | 0.159917 | 0.020888 | 0.339245 | 0.864343 |

The frontier threshold is 0.20. The separate weather policy uses 0.15 and
blends 25% weather with 75% geometry probabilities. Parameters were fixed
before fitting; no held-out tuning was performed. The weather point estimates
reproduce the reference policy; the frontier is a new fitted model.

Both saved models reproduced their calibration probe probabilities exactly
after reload. A real-model API seed, 12-hour step and repeated identical
request passed, including deterministic replay with the CSV-only terrain
provider. Unknown terrain remained missing.

These are reused incident/region/time benchmarks with satellite-dependent
weak labels. They are not an independent new test or operational validation.
Road features had insufficient historical support and were not fitted.

Source release manifest SHA-256: `f3f3fbb393c205d6fe2a9fc2ed05a508258b919e329f48b3ed48759356467f69`.
Full protocol, source digests, artifact digests and metrics are in the local
`artifacts/public-csv/` directory; generated artifacts are not committed.
