# Training CSVs: May 11–August 22, 2026

Plain, uncompressed CSV exports of the latest retained training feature tables
for **2026-05-11 through 2026-08-22, inclusive**, using the expanded
FIRMS/FEDS boreal release (500,364 candidate examples). This is the common
release used by the weather, vegetation, and landscape experiments.

The range refers to `source_snapshot_time`, the FEDS source snapshot date.
`anchor_at` and `feature_cutoff_at` use the existing cell-local time alignment;
weather hours and 12-hour target endpoints can extend beyond those calendar
dates. Static terrain and vegetation have their own source vintages.
These are the retained candidate snapshots in that interval, not a claim of
complete geographic or twice-daily observations everywhere.

## Files

| CSV | Rows | Size (decimal MB) |
| --- | ---: | ---: |
| [candidate_examples.csv](candidate_examples.csv) | 500,364 | 2,913.62 |
| [unscored_positives.csv](unscored_positives.csv) | 35,265 | 199.97 |
| [weather_features.csv](weather_features.csv) | 500,364 | 267.84 |
| [weather_history.csv](weather_history.csv) | 500,364 | 83.12 |
| [directional_features.csv](directional_features.csv) | 500,364 | 167.45 |
| [vegetation_features.csv](vegetation_features.csv) | 500,364 | 367.11 |
| [landscape_features.csv](landscape_features.csv) | 500,364 | 73.14 |
| [incident_assignments.csv](incident_assignments.csv) | 500,364 | 100.58 |

Total CSV size: **4.17 GB** (3.89 GiB).

- **`candidate_examples.csv`** — The main table: one candidate 1 km cell at one
  prediction origin. Includes FIRMS satellite fire detection counts, brightness,
  recency and neighborhood features; ETOPO elevation (metres), slope (degrees),
  aspect sine/cosine and terrain coverage; coordinates; the 12-hour label;
  original chronological split; and full source lineage. Terrain is already
  included here, so a separate terrain CSV is unnecessary.
- **`weather_features.csv`** — Hourly Open-Meteo ECMWF IFS historical analysis
  mapped to each candidate at its anchor hour: temperature (°C), relative humidity
  (%), precipitation (mm), eastward/northward wind components (m/s), and weather
  time/tile/grid/source provenance. This is retrospective weather, not an issued
  forecast available at the historical prediction origin.
- **`weather_history.csv`** — Rain totals, humidity/VPD summaries, and wind
  history for each example. Includes 6-hour and 24-hour fields. The selected
  weather model uses the 6-hour fields; 24-hour coverage was insufficient and
  those fields were excluded from fitting. Missing windows remain missing.
- **`directional_features.csv`** — Observed nearby-fire geometry (5×5 counts,
  nearest-fire distances and ages, distance-weighted counts, recent fraction)
  and wind projected from observed fires toward the candidate. Distances are
  km, ages are hours, and wind projections are m/s. These describe the observed
  state; do not attach them unchanged to synthetic fire rollouts.
- **`vegetation_features.csv`** — NALCMS class fractions, MOD44B tree/non-tree/
  nonvegetated cover, valid-area and missingness flags, source ages, QA caution
  fraction, and vegetation lineage. Projected from the expanded vegetation
  release, retaining example identity and all `vegetation_*` columns without
  duplicating its fire/terrain columns. This experiment uses retrospective
  static context and did not pass model promotion gates. Cover values are
  fractions, not categorical fuel-model codes or measured fuel mass.
- **`landscape_features.csv`** — Corrected native-resolution landscape pilot
  features: cover fractions, road length and distance, known-width/surface
  fractions, developed-land distance, coverage flags, and bundle checksum.
  Only **484** examples have eligible land-cover support and **zero** have
  historically eligible road support. September captures cannot establish
  May–August road geometry. This table is retained for research/coverage
  inspection; the support gate failed and no landscape model was fitted.
  Missing road features do not mean that roads were absent.
- **`incident_assignments.csv`** — Per-example incident group, member/source
  identities, region, assignment reason, and `incident_split`. These are the
  incident/region/time cohorts used by later experiments, including training,
  calibration, held-out groups, and unassigned examples. These are metadata,
  not predictor inputs.
- **`unscored_positives.csv`** — 35,265 FEDS-positive examples outside the
  FIRMS-supported candidate table. Diagnostic coverage evidence only;
  **exclude these from binary fitting**. Their IDs are disjoint from the
  candidate examples.

## Reading and joining

All feature and assignment tables contain the same 500,364 unique candidate
`example_id` values. Join one-to-one on `example_id`, checking shared cell,
timestamp, target and split fields when present. The export checked those
fields against the base table. Do not join solely on `cell_id`: a cell can
appear at multiple prediction times. The history table's dates are inherited
through its verified example IDs.

For example, load only the columns you need from the large base CSV and add
hourly weather without duplicating its identity fields:

```python
from pathlib import Path
import pandas as pd

folder = Path("releases/training-csvs-2026-05-11_to_2026-08-22")
base_columns = ["example_id", "cell_id", "source_snapshot_time",
                "target_newly_burned_12h", "terrain_elevation_m"]
frame = pd.read_csv(folder / "candidate_examples.csv", usecols=base_columns)
weather = pd.read_csv(folder / "weather_features.csv")
weather_columns = ["example_id", "weather_temperature_2m",
                   "weather_relative_humidity_2m", "weather_precipitation",
                   "weather_wind_u_10m", "weather_wind_v_10m"]
frame = frame.merge(weather[weather_columns], on="example_id",
                    how="left", validate="one_to_one")
```

CSV strings, nested compact JSON, blank values and flags retain their source
semantics. Parse nested lineage with `json.loads` where needed; preserve blanks/
NaN rather than replacing missing observations with zero. The base release's
`weather_available` flags describe its original weather-free build; joining a
weather sidecar does not rewrite that provenance.

Use the model's explicit feature allowlist, not every numeric CSV column.
Targets, IDs, dates, split assignments and future-label evidence are not input
features. `metadata/base_schema.json` and the copied source manifests contain
the original column and feature contracts; `manifest.json` lists every exported
column. The weather trainer additionally computes instantaneous wind speed and
VPD from the hourly fields in memory.

These 500,364 candidates include evaluation and unassigned examples, not just
rows used for fitting. Preserve the relevant experiment's partitions. The
original `dataset_split` is chronological; later experiments use
`incident_split` and additional eligibility/frontier filters. The selected
weather experiment fitted 20,940 observed frontier examples and used 3,403
separate calibration examples. Do not randomly split neighboring observations
or fit held-out/unassigned rows to reproduce that experiment.

`target_newly_burned_12h=1` denotes a weak FEDS/FIRMS-supported positive;
`0` is a FIRMS-seeded weak-negative proxy, not verified absence of burning.
FEDS and FIRMS share satellite evidence and are not independent ground truth.

## Scope, provenance and verification

The previously discussed roughly 18.9 GB is a source-archive/storage figure,
not the size of the training CSVs. National road GeoParquet, source terrain and
vegetation rasters, raw fire/weather responses, and simulation caches remain
in the source archive. The model samples/aggregates those formats into selected
features; it does not turn the whole archive into training CSVs. In particular,
the current national road archive is simulation evidence, not historically
eligible May–August training observations.

This folder contains actual independent CSV files, not symlinks. Seven files
are lossless decompressions of their source CSVs; vegetation is a documented
column projection. Earlier smaller releases, superseded pilots, synthetic
augmentation tables and prediction/metric outputs are excluded. No source
data, trained model, or training configuration was modified.

`manifest.json` records source paths and SHA-256 hashes, copied manifest paths,
exported column lists, row counts, file sizes, date bounds, split/target counts,
and missing-flag counts. Manifests under `metadata/` use repository-relative
paths; local account names and absolute workspace prefixes have been removed.
Those paths need not exist on another machine to read these CSVs.
`source_manifest_sha256` verifies the packaged, sanitized manifest;
`original_source_manifest_sha256` preserves the original artifact digest.
Hashes inside the source manifests continue to identify the original source
artifacts. Raw source evidence is not bundled here.

See [the privacy review](PRIVACY_REVIEW.md) for the public-export audit and
metadata sanitization. `SHA256SUMS` covers all other files in this folder.
Verify after copying:

```bash
cd releases/training-csvs-2026-05-11_to_2026-08-22
sha256sum -c SHA256SUMS
```

To reproduce the CSVs and metadata from the retained local sources, run from
the repository root with a new destination (existing directories are refused):

```bash
.venv/bin/python \
  releases/training-csvs-2026-05-11_to_2026-08-22/metadata/export_training_csvs.py \
  releases/training-csvs-rebuilt
```

That script verifies source hashes, unique IDs, complete joins, shared identity
fields, date bounds and row counts before publishing its completion manifest.
This README and its folder-wide checksum list are packaging documentation
added after the export.
