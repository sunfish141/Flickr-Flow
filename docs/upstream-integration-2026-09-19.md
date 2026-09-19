# Upstream integration — 2026-09-19

`main` was fast-forwarded from `9ab6168` to `4cbd8e6`, incorporating all eight
incoming commits. Local uncommitted work was reapplied and conflicts reconciled;
no local changes were committed or pushed. Recovery stash:
`c39280db13f99746e69edadda7c3a6e4f2f9c0d1` (`pre-upstream-integration-2026-09-19-online-map-fix`).
The older pre-existing stash was retained as well. Ignored credentials, training
archives and user scenario storage were not included in the stash or altered.

## Reconciliation

- Kept the requested one-map UI, automatic online/offline handling, installed
  pack outlines and labelled online 1 km fallback; did not restore the old
  simulator selector, large explanatory text or intensity slider.
- Integrated upstream polygon-cell inspection, fuel-dependent coarse burnout,
  native-raster water checks, weather-hybrid development APIs, road preloading,
  incremental arrival searches and geometry optimizations.
- Weather-hybrid mode remains opt-in and requires its separate artifacts and
  expanding landscape archive; the pilot desktop does not automatically enable
  it, fetch weather or download national archives.
- Upstream web playback now extends beyond 96 hours. The durable planning
  contract still caps scenarios at 96 hours. Longer playback is not validation.
- Engine v5 blocks continuation of saved v3 results while retaining viewing and
  export. Import normalizes the new optional residence-policy field for
  comparison without rewriting the original saved assumptions or frames.
- The desktop build now explicitly includes and resolves `config/fuel_policy.json`.
- Windows road-archive verification rehashes partitions rather than trusting a
  size/mtime/ctime cache key; Arrow preload still avoids repeated Parquet decoding.
  This is an integrity-first I/O tradeoff for the optional national-archive path,
  which is not enabled in the two-pack pilot. Python's Windows timestamp behavior
  is documented in [the Python reference](https://docs.python.org/3.12/library/os.html#os.stat_result).

## Verification

The integrated-map browser checks (including upstream polygon-cell inspection)
and 24 frontend unit tests passed. The broad backend run passed 277 tests and 54
subtests. Source desktop acceptance also passed, including native rendering,
offline cold start and worker interruption recovery. Results are recorded under
`artifacts/upstream-merge-tests.log` and `artifacts/desktop-smoke/source/`.
The broad Windows run excludes `tests/providers/test_startup_data.py`: that
legacy preparation module imports Unix-only `fcntl`. Preparation is disabled
in the packaged runtime; this is not evidence that the preparation tools are
Windows-portable. No live weather/FIRMS requests or automatic source downloads
were used in these checks.

The merged Windows EXE was rebuilt and passed frozen desktop acceptance,
including polygon inspection, online grid placement, offline cold start and
interruption recovery. Bundle digest:
`83372f4ab069d0c441e3e1a6d6a9ca93826c6d2c01403e83552458dd348f2a5b`.
Measured results are in [desktop acceptance](desktop-acceptance.md). The
pre-merge handoff archives remain available; no previous release was deleted.
