# Feature ablations and moisture-data pilot

Completed September 19, 2026. The current target is a laptop with 8–16 GB RAM.
Phone export and benchmarking are deferred. No production model was changed.

The main finding is that calendar phase explains most of the earlier added
context gain. Extra fire-pattern ratios alone add little. Moisture context is
a better next experiment than increasing model capacity or adding every weather
variable. This is development evidence, not an independently confirmed gain.

## What ran

The training-only runner compared 12 feature sets using both the compact
100-iteration and reference 300-iteration gradient-boosted models. Three grouped
incident folds and three forward-time folds give **144 fits**. Fitting, scoring
and report generation took **62.71 seconds** on two CPU threads, excluding
release verification/loading. No calibration or published holdout rows were
used to fit or score this round. No final model or alert threshold was selected.

The applicable training cohort is **20,933 rows**. Seven of the original 20,940
training rows were excluded because their target windows reach the later-test
boundary, even though their source snapshots precede it. The original experiment
runner now also excludes such training/calibration windows. Round-one artifacts
are preserved as recorded and have not been rerun with this correction. Their
reported scores should not be mixed with this round's scores as a paired test.
Dropping those rows also changes the deterministic grouped fold assignments.

Forward validation uses only earlier completed labels, adds another 12-hour
embargo, and removes every validation incident from that fold's training set.
This tests later unseen incidents, not continuation of a known fire.

| Validation period (UTC) | Training rows / incidents | Validation rows / incidents | Positive fraction |
| --- | ---: | ---: | ---: |
| June 20–27 | 2,302 / 29 | 3,363 / 50 | 46.27% |
| June 28–July 5 | 3,057 / 34 | 10,314 / 77 | 51.11% |
| July 6–19 | 5,798 / 60 | 3,472 / 69 | 32.29% |

The same incident can occur in different validation periods, so these fold
scores are not independent replications. No confidence claim is made from
three folds. All results below use the same compact architecture and matched
rows within a fold, separating feature effects from capacity changes.

## Results

AP is average precision, a ranking metric, not classification accuracy.
The columns are unweighted means of fold scores. Compare feature sets within
each validation design; different periods have different positive fractions.

| Feature set | Inputs | Grouped AP | Forward AP | Forward recall among top 20% of rows |
| --- | ---: | ---: | ---: | ---: |
| Frontier | 13 | 0.6482 | 0.6201 | 32.89% |
| Geometry | 21 | 0.6725 | 0.6390 | 32.63% |
| Geometry + five fire-pattern features | 26 | 0.6732 | 0.6388 | 32.64% |
| Geometry + calendar phase | 23 | 0.6989 | 0.6468 | 33.22% |
| Full offline context | 28 | 0.6977 | 0.6477 | 33.30% |
| Geometry + moisture | 25 | 0.6889 | 0.6418 | 32.57% |
| Geometry + wind | 30 | 0.7015 | 0.6340 | 31.75% |
| Context + moisture | 32 | 0.7072 | 0.6596 | 33.67% |
| Context + all weather | 41 | 0.7310 | 0.6583 | 33.47% |
| Geometry + vegetation | 43 | 0.7103 | 0.6450 | 33.47% |
| Context + vegetation | 50 | 0.7086 | 0.6429 | 33.31% |

The five fire-pattern additions are neighborhood concentration, outer-ring
detections, detections per active cell, brightness contrast and older detections.
Calendar phase is sine/cosine of day of year. Moisture here means relative
humidity, current precipitation, vapor pressure deficit and six-hour rain,
not the new soil-moisture pilot. Wind includes current speed, source-to-candidate
projections and five six-hour wind statistics.

Interpretation:

- Most of the offline-context gain over geometry is reproduced by just the two
  calendar features. With only one summer, this may encode season progression,
  incident mix or collection effects. Another year is needed before relying on it.
- Adding moisture to context raises mean forward AP from 0.6477 to 0.6596, but
  loses in the first period: 0.6568 to 0.6379. It improves the other two periods
  from 0.7274 to 0.7463 and 0.5588 to 0.5947. It is promising, not uniformly better.
- Wind helps grouped validation more than time-ordered validation. All-weather
  context has the strongest grouped scores, but does not beat moisture-only
  context on mean forward AP. Internet access is not a reason to automatically
  switch to a richer model.
- The 300-iteration context/moisture model scores 0.6602 forward AP, essentially
  the same point estimate as compact's 0.6596. There is little justification here
  for a much larger model. Vegetation also adds no clear benefit on top of context.
- Ranking gains do not guarantee alerting gains. At the illustrative top-20%-row
  budget, context/moisture captures 33.67% of positives versus geometry's 32.63%.
  This pooled candidate-row diagnostic is not a geographic area budget or a
  deployable warning rule. Cutoff ties receive fractional expected credit.

These are still satellite-dependent weak labels. Forecast availability,
calibration, recursive simulation and independent perimeter validation remain
separate requirements.

## Practical extra data

**First choice: longer moisture history from the existing weather provider.**
Open-Meteo's historical endpoint exposes hourly precipitation, soil water,
humidity, vapor pressure deficit and gusts. This avoids introducing a second
provider for the first experiment. Its historical IFS grid is approximately
9 km, so these are regional context rather than fine-scale fuel measurements.
[Provider documentation](https://open-meteo.com/en/docs/historical-weather-api).

The current release's six-hour histories are complete in the original training
cohort, while its three 24-hour moisture-history fields are populated on only
1.72% of those rows. A longer history needs a real backfill; missing rain must
not be treated as zero.

I implemented and ran a small, separate feasibility probe at three existing
training source-grid coordinates in Mexico, Quebec and Manitoba. Each response
contains 288 hours, June 20–July 1, and all three locations support all eight
derived features at the July 1 12:00 UTC cutoff:

- precipitation over the preceding 24, 72 and 168 completed hours;
- minimum humidity and mean vapor pressure deficit over 24 hours;
- maximum gust over six hours;
- current shallow and deeper soil-water proxies (0–7 and 7–28 cm).

The three raw responses total **44,125 bytes**. They are cached with request
parameters, retrieval times, units, source coordinates and SHA-256 hashes.
No training labels or credentials are transmitted. A TLS handshake timeout
interrupted the initial collection after one location; the verified resume
path completed the remaining two without redownloading or overwriting it.

This proves accessibility for these sample locations/dates only. The new
features are **not yet joined to training rows or tested for predictive benefit**.
The three locations were a feasibility sample, not a representative accuracy set.
Soil water is not a direct measurement of live or dead fuel moisture.

**Second choice for a Canadian pilot: CWFIS fuel-moisture indices.** FFMC, DMC
and DC describe different fuel-moisture layers, with ISI/BUI/FWI providing related
fire-behavior indices. These are a targeted alternative to generic extra weather.
The public download archive is accessible, but a suitable location/date join,
publication timing, missing coverage and applicable source terms still need
checking. This source has not been downloaded or added to the model.
[NRCan definitions](https://cwfis.cfs.nrcan.gc.ca/downloads/cffdrs/fwi_grids_metadata_NAP_ISO_19115_2003_EN.pdf),
[observation archive](https://cwfis.cfs.nrcan.gc.ca/downloads/fwi_obs/).

## Offline and resource implications

Historical analysis retrieved today is not evidence of what a device knew at
a past prediction time. The new history builder excludes hours after its feature
cutoff and refuses incomplete windows, but the retrospective source can still
contain information unavailable operationally. It remains research-only.

For operational experiments, archive issued forecasts and past observations
with actual download/publication times. Open-Meteo's Single Runs API can retrieve
specific forecast runs, but its `run` timestamp is initialization, not release
time; the provider documents a further calculation/distribution delay. A replay
must account for that delay. [Single Runs documentation](https://open-meteo.com/en/docs/single-runs-api).

The intended offline behavior remains: local models and regional terrain work
without internet; weather-enhanced prediction requires a previously downloaded,
validated, sufficiently fresh local cache. Missing, expired or spatially
unsupported weather falls back to the offline model. Cached fire detections also
need explicit age/coverage handling. The app's complete offline UI/packaging has
not been implemented by these experiments.

The 15–20 GB weight allowance is far above present needs: round-one classifiers
were approximately 0.1–0.4 MB. The original CSVs plus preserved ZIP occupy about
4.73 GB. Data packs, Python/GIS runtime and maps, not classifier weights, are the
storage considerations. Round-one training was observed at about 1.72 GiB peak
working set; during this run Windows reported a lifetime peak of 1,304,780,800
bytes at the inspection point. Neither observation is a full-application memory
guarantee or a direct test on an 8 GB machine. No extra hardware budget is needed
for the current experiment pipeline.

Keep requests deduplicated by source grid and bounded by region/date. The provider's
free service is for noncommercial use, with rate limits and no uptime guarantee;
commercial deployment has separate plans. No paid service was used or enabled.
[Provider plans](https://open-meteo.com/en/pricing).

## Reproduction and next decisions

Run commands are in [the experiment guide](experiments.md). Immutable results:

- `artifacts/experiments/round-02-ablations/`: frozen protocol, all fold scores and report;
- `artifacts/experiments/weather-history-pilot-01/`: raw cached responses and derived features.

Verification: all 36 focused model/data/history tests passed. All eight artifacts
listed across the two completed-run manifests passed SHA-256 verification.
Original CSVs were not modified; raw inputs remain separate from derived results,
and missing observations are not replaced with measured zeros.

Next, backfill a bounded set of training source grids, join by exact source grid
and cutoff, and compare moisture features under this same grouped/forward protocol.
Then validate using genuinely new incidents or another season. Avoid further
selection against the already inspected round-one holdouts. PCA is not a priority
for these small feature sets; feature ablation is currently easier to interpret.

The useful user decisions are the first deployment region/community and whether
the product is scenario exploration or an alerting tool. Alerting also needs an
agreed missed-fire versus false-alert tradeoff. Phone/OS decisions can wait.
