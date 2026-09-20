# Full Alberta and Colorado offline coverage

The earlier Windows preview bundled only two 81 km² pilot areas. The web code's
Alberta/Colorado presets referred to a separate national archive, which was not
present on this Windows machine. Git transfers code, not that ignored archive.

## Sources and preparation

`scripts/prepare_regional_inputs.py` retains source checksums, capture timestamps,
transforms and per-feature road provenance. It uses the already retained,
audited NALCMS v2 ZIPs: Canada component year 2020 and USA component year 2021.
These are the reviewed snapshots used by the pilot builder, not substituted
copies of the missing older national archive.

Territorial boundaries and roads use the pinned Overture release `2026-08-19.0`:
[division-area schema](https://docs.overturemaps.org/schema/reference/divisions/division_area/)
and [transportation extraction documentation](https://docs.overturemaps.org/guides/transportation/).
Boundaries use CA-AB and US-CO region features; the simulation domain is the
actual published polygon, not the larger road-extraction rectangle. The latter
has a buffer to retain road centerlines near coverage edges. Overture and its
upstream contributors require attribution; road and division source records
are retained. Public distribution still needs the licensing review.

Preparation streams nearest-neighbor 30 m projected rasters in 512-pixel blocks
and collects spatially filtered roads into bounded-memory Parquet. National
ZIPs and temporary source TIFFs are preparation inputs only. Completed files
are retained and checked on retry; unknown land-cover and road attributes are
not replaced with guessed measurements. A catalog is published only after both
regional manifests exist. Runtime never downloads or converts national archives.

The prepared inputs occupy approximately 718 MB. The buffered extraction contains
880,614 Alberta road records and 1,366,957 Colorado road records; these are source
segments, not counts of distinct roads. Simulation and display are clipped to
the actual regional boundaries, including where the extraction halo extends
outside them. The road source can still omit real roads or their attributes.

For automated builds, `scripts/obtain_regional_build_input.py` accepts an HTTPS
`DESKTOP_REGIONS_URL` and an independently reviewed `DESKTOP_REGIONS_SHA256`.
The data-only ZIP must contain exactly `index.json` plus `manifest.json`,
`boundary.geojson`, `land-cover.tif`, and `roads.parquet` under each of `alberta/`
and `colorado/`. Both compressed and unpacked inputs are capped at 2 GB. Unknown
paths, duplicates, links, changed checksums and existing destinations are rejected.
Configure those runner variables only after publishing the reviewed artifact;
this local implementation does not upload the data or assume runner access.

## Runtime

The installed bundle holds immutable rasters, road Parquet, geographic boundaries
and checksummed manifests. Sources are verified at startup. Offline display
uses local PNG tiles; roads are drawn at zoom 12 and higher. Unknown/urban cover
is purple. The general Natural Earth overview remains outside installed areas.

The existing uncalibrated travel engine receives 3 km landscape tiles, read only
around an ignition and its subsequent spread. The computational mesh remains
explicitly 100 m. Cache admission shares the SQLite store's 20 GB total budget,
which includes the installed distribution; writes go to OS user data. Cache
eviction limits in-memory samplers but never deletes saved files. Each run is
bounded to 128 tiles and 500,000 patches, not an entire province in memory.

Boundaries clip fuel geometry and stop modeled spread; outside coverage is not
safe or nonfuel. Water, urban mixtures, unknown cover and road barriers can
reject ignition placement. Historical land cover is not current fuel condition;
general ember crossing, buildings and operational fire forecasts remain unmodeled.

## Verification

Synthetic tests cover checksum rejection, coverage clipping, deterministic
boundary termination, invalid map requests, user-directory writes and storage
failure. Browser checks exercise automatic regional routing and fully loaded
local map tiles, with zero external requests offline.

The rebuilt Windows EXE passed offline 24-hour runs in northern and southeastern
Alberta and southwestern and eastern Colorado, far from the original pilot
squares. Same-input replay reproduced identical frames. Actual political-boundary
rejection, interrupted legacy-case recovery, native Qt rendering and six browser
viewport sizes also passed. This is development-machine verification, not an
independent clean installation or fire-behavior validation. Measurements and
remaining gates are recorded in `desktop-acceptance.md`.
