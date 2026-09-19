# First measured experiment round

Follow-up: [round two](experiment-round-02.md) reports feature ablations and a
moisture-data pilot under the updated laptop-first target. It also identifies
seven training label windows crossing the later-test boundary; the current
runner excludes such windows. The original results below are preserved as run,
not a corrected rerun. Phone-specific next steps below are now deferred.

Completed September 19, 2026 on the downloaded, checksum-verified release.
The experiment compared five model configurations across five feature profiles,
with three incident-grouped development folds: 75 development fits, followed by
10 final classifiers and their separate calibrators. All candidates completed.
The development fitting/scoring loops took 21.39 seconds in total; this excludes
CSV verification/loading, final fits, bootstrap evaluation and serialization.

The compact frontier model is a promising low-cost replacement candidate. The
offline context profile gives the strongest region/later-time ranking scores
among the development-selected models. Neither has been promoted into the map;
recursive behavior and deployment still require validation.

## Data and reproducibility

| Resource | Location |
| --- | --- |
| Eight original CSVs, 4,172,833,952 bytes total | `htn_training/` |
| Original ZIP, retained without modification | `data/downloads/training-release-2026-05-11_to_2026-08-22.zip` |
| Protocol, scores, models and measurements | `artifacts/experiments/round-01/` |
| Readable generated report | `artifacts/experiments/round-01/summary.md` |

All three data/artifact locations are excluded from Git. The ZIP was moved from
the repository root; it was not deleted. CSV bytes matched the existing release
manifest before fitting. All 15 completed-run artifacts passed hash verification.

Release manifest SHA-256:
`f3f3fbb393c205d6fe2a9fc2ed05a508258b919e329f48b3ed48759356467f69`.

Original ZIP SHA-256:
`1af98b60da2b3fc9aa23dd195a4c814663f97f668f64f9381b3b9bbc86e7d371`.

There are 500,364 candidates. The applicable frontier cohort contains 20,940
training rows from 135 incidents, and 3,403 calibration rows from 21 different
incidents. The 35,265 diagnostic positives outside candidate support remain
excluded. All five feature profiles use the same training/calibration rows.

| Holdout | Rows | Incidents | Positive fraction |
| --- | ---: | ---: | ---: |
| Held incident | 9,177 | 55 | 44.50% |
| Held region | 12,318 | 60 | 36.24% |
| Later time | 37,743 | 196 | 25.39% |

Model selection used only mean development PR-AUC (average precision), within
the declared training incidents. Calibration followed selection. The separate
test cohorts were evaluated after all selections were saved. The table below
reports those selections even where a reference model scored better on a test
cohort; no test-driven reselection was performed.

The local environment uses Python 3.12.8 and scikit-learn 1.7.0, pinned in
`requirements-experiments.txt`. Earlier reconstruction results used a different
environment and are not numerically identical. Comparisons here use reference
models refitted in the same run, rather than mixing scores from different runs.

## Accuracy and cost

PR-AUC is a ranking metric, not percent of cells predicted correctly. Higher
is better, but it must be considered alongside calibration and the operating
threshold. Different holdouts also have different positive fractions.

| Profile / model | Features | Development PR-AUC | Held incident | Held region | Later time | Model KB | p95 ms / 1,024 rows |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Frontier reference, 300 trees | 13 | 0.6487 | 0.6787 | 0.6010 | 0.4644 | 290.3 | 14.71 |
| Frontier selected, 100 trees | 13 | 0.6520 | 0.6796 | 0.6053 | 0.4744 | 107.9 | 4.64 |
| Geometry selected, 100 trees | 21 | 0.6754 | 0.7078 | 0.6354 | 0.5062 | 121.1 | 4.60 |
| Offline context selected, 200 trees | 28 | 0.7071 | 0.7295 | 0.6741 | 0.5513 | 403.8 | 16.54 |
| Weather selected, 100 trees | 34 | 0.7145 | 0.7559 | 0.6372 | 0.4730 | 144.8 | 5.01 |
| Vegetation selected, 200 trees | 43 | 0.7167 | 0.7374 | 0.6605 | 0.5113 | 432.0 | 15.31 |

The 100-tree frontier model is 62.8% smaller than the 300-tree reference and
3.04 times faster by median batch latency (4.12 versus 12.54 ms). Its point
estimates are slightly better on all holdouts. Its paired whole-incident
bootstrap interval for later-time PR-AUC improvement is [0.0055, 0.0133]; the
incident and region intervals include zero. These intervals use 200 resamples.

The larger gain comes from feature context. Compared with the newly fitted
frontier reference, the selected context model improves the three PR-AUC point
estimates by 0.0508, 0.0731 and 0.0869. Those comparisons include both a feature
change and a model-capacity change. The context profile with the *unchanged*
reference architecture also improves all three scores to 0.7257, 0.6788 and
0.5462, supporting further study of the features themselves. This report does
not provide cross-profile confidence intervals or claim independent confirmation.

The added context includes neighborhood concentration, counts in the outer
ring, older detections, brightness contrast and annual phase. The current
experiment does not isolate which of these causes the gain. Annual phase in
particular needs an ablation and another season to distinguish useful seasonality
from a shortcut specific to this summer's data.

Logistic and binned-logistic candidates did not win any profile's development
comparison. There is no evidence from this round that a new neural architecture
is necessary to meet the model-size target.

## Tradeoffs that prevent automatic promotion

At the fixed 0.20 threshold, later-time context recall is 62.0%, versus 74.0%
for the frontier reference and 78.6% for selected geometry. Context precision
is higher (49.8% versus 33.8% and 37.0%), but it misses more positives at this
cutoff. Its later-time ECE is also worse: 0.0723 versus 0.0505 for the frontier
reference and 0.0277 for selected geometry. Better ranking does not yet imply
better calibrated warnings or a suitable alert threshold. Any threshold changes
must be selected using development/calibration data, not these test results.

The weather model wins on held incidents but is weak on later time: 0.4730
versus 0.5062 for geometry without weather. The existing fixed 75% geometry /
25% weather reference blend scores 0.7377 / 0.6482 / 0.5055 across the three
holdouts, showing a more balanced result than using weather alone. The compact
weather model loses to its same-profile reference on region and later-time
benchmarks; the paired bootstrap intervals for those differences are below zero.
Internet access should therefore not automatically switch the app to this model.

Vegetation improves on basic geometry in several comparisons, but its selected
configuration loses later-time PR-AUC relative to the vegetation reference
(0.5113 versus 0.5197), and the source is retrospective static context. It remains
an experiment rather than a promoted dependency.

All holdout rows contain the core historical weather inputs. The complete-outage
evaluation uses geometry alone, but partial-outage behavior was tested with
synthetic fixtures, not measured across naturally missing weather in this release.
Historical analysis weather is not an issued forecast. The input-availability
policy still requires separate forecast validation before enabling cached-weather
inference in the app.

## Hardware and limits of the measurements

Measurements were made on an Intel Core Ultra 5 125U laptop with about 15.4 GiB
physical RAM, with two numerical-library threads. Artifact sizes are decimal KB
for uncompressed Python joblib files, not native mobile exports. Batch timings
include classifier plus calibration and exclude feature construction, map/GIS
processing and network. Twenty warmed repetitions were measured per model.
All saved models reproduced their probe probabilities exactly after reload.

The training process's Windows-reported lifetime peak working set observed
during fitting/evaluation was 1,759.6 MiB. This includes release loading and
dataframes, and is not an inference-only memory budget or guaranteed final
lifetime peak. Phone latency, application/runtime size, inference RAM, battery
consumption and full recursive simulation performance remain unmeasured.

The release uses satellite-dependent weak labels and reused incident/region/time
benchmarks. These are useful comparative research results, not an independent
operational validation.

## Next steps

1. Treat the compact 13-feature frontier model as the first deployment candidate:
   validate export parity and recursive behavior before changing the map model.
2. Study the offline context features with grouped/temporal development ablations,
   particularly with and without annual phase, then obtain fresh incident/season
   evidence. Repeated tuning on the displayed holdouts would weaken them as tests.
3. Choose a warning operating point on development data and compare recall at a
   fixed false-alert or predicted-area budget, rather than retaining 0.20 by habit.
4. Benchmark the export and feature pipeline on a named phone. Then package
   bounded regional terrain/cover/map data, preserving source age and coverage.
5. Add and validate issued forecast inputs and cache-expiry fallback before using
   weather in recursive scenarios.

The useful user inputs for that deployment work are the target phone/OS and the
first geographic region/community. An alerting use case also needs a decision
about the acceptable missed-fire versus false-alert tradeoff. Nothing else is
needed to reproduce this completed first experiment round.
