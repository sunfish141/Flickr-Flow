# Offline Planning v1: internal acceptance record

Date: 2026-09-19. Scope: Windows research pilot, **not field-ready or operationally
validated**. The latest upstream `main` was fetched and fast-forwarded to
`9ab6168b11a60b160d7a05239ca773a16dd2a87f`. Existing local ML/data work was retained.
This implementation remains local and uncommitted; nothing was pushed or published.

## Delivered locally

- A bundled Python service and compiled React interface, launched on loopback in
  the installed browser. No end-user Python, Node, account or credentials.
- Two verified, aligned 9 × 9 km public-data packs with local vector maps. No remote
  tiles, startup collection, ML artifact dependency or automatic online provider.
- Named SQLite cases, acknowledged input saves, atomic frame checkpoints, revision
  checks, duplicate-request handling, cancellation and forced-termination recovery.
- Manual ignitions, UTC origin time, meteorological wind-from controls, immutable
  results, cloned variants, synchronized comparisons and durable paused playback.
- Definition/provenance/results JSON import/export, standalone HTML/SVG reports and
  GeoJSON. Imports are strictly validated and marked unverified; no executable models.
- Managed-storage admission checks with no automatic case/pack deletion, bounded
  workers, pinned dependencies, preparation tools and unsigned per-OS build workflows.

Start with `READ-ME-FIRST.md` in the release ZIP, or the repository's
[quickstart](offline-planning-quickstart.md). Keep the complete application folder.

## Data evidence

| Pack | Total coverage | Supported vegetation | Urban/unknown, unsupported | Unknown road width / grade |
| --- | ---: | ---: | ---: | --- |
| Hinton, Alberta | 81 km² | 69.80 km² | 7.91 km² | 947 / 1033 of 1060 pieces |
| Black Hawk, Colorado | 81 km² | 75.86 km² | 4.98 km² | 1114 / 1107 of 1120 pieces |

Pack-manifest SHA-256:

- Hinton: `2711c99136aea2a95d278ea4bff008afa32790578162f781cbc92f0dbcc3cf6a`
- Black Hawk: `a7f5f5299a55251265f4dc254a0f3ce76f8d370974fed21eb0a6f45f88e9fa30`

Both use NALCMS Ed. 2.0 and Overture `2026-08-19.0`. Canada cover is labeled 2020,
with some 2019/2021 imagery; CONUS cover is 2021. The current official ZIPs differed
from the old project's hashes. Their embedded metadata was audited, new snapshots
explicitly pinned, and the old references retained rather than silently substituted.
See [source audit and provenance](offline-planning-v1.md#audited-source-snapshot-change-2026-09-19).
Unknown road evidence was preserved. Neither pack represents current fuel moisture
or validates wider North American, European or remote-community coverage.

National preparation archives and original training data remain separate from
installed storage. The bundled prepared packs together occupy about 2.7 MB.

## Automated verification

Targeted suites: **99 passing checks** (not a claim that the entire legacy suite ran):

- 34 planner checks: revision conflicts, idempotence, checkpoint transactions,
  cancellation races, interrupted-write rollback, simulated disk-full/quota failures,
  retained results with missing/changed resources, imports, report escaping,
  cross-origin/host rejection, instance locks, network denial and safe pack extraction.
- 21 local-spread and preparation regression checks, including preservation of
  missing/ambiguous road attributes after merging upstream changes.
- 16 frontend checks, including comparison compatibility and existing UI regressions.
- 28 existing ML experiment/ablation/weather-probe checks in the original environment.

Real-pack browser acceptance exercises both pilot regions: baseline plus wind
variant, a second editing tab, reload during calculation, 25 hourly frames per
24-hour case, policy/cache isolation, saved comparison playback and all three exports.
A deliberately delayed older playback response tests that a newer seek remains
queued and is never falsely labeled saved. Page requests outside loopback are
denied; the expected result is no attempted external page requests or script errors.
Accessibility automation excludes color contrast and does not establish full
accessibility compliance.

Both real packaged-browser JSON exports also passed import and re-export through
a fresh source API store using the same pinned runtime: all 25 frames, definitions,
map snapshots and saved playback positions were retained exactly. Creation instants
were preserved (UTC `Z` and `+00:00` spellings can normalize). Imported results stayed
unverified and could not be continued as trusted calculations. This is not yet a
cross-platform round-trip test.

The process-interruption harness kills the service and its worker without graceful
shutdown, reopens the same Unicode/space-containing data directory, and verifies
that all acknowledged checkpoints survive. It resumes the interrupted case to
96 hours and compares its first 25 frames exactly with the earlier 24-hour baseline.
For matching engine dependencies, declared comparison tolerances are 1e-6 m² in
areas and 1e-9 degrees in coordinates; cross-platform numerical equivalence is not
yet claimed.

Reproducible commands and environment separation are documented in
[engineering notes](offline-planning-v1.md#builds-and-testing). Raw local evidence is
under `artifacts/planning-smoke/`; the handoff includes the final packaged-run reports.

## Measurement scope

Final packaged acceptance completed at `2026-09-19T18:43:58Z`:

| Measurement | Observed locally |
| --- | ---: |
| Application plus bundled packs | 94,794,862 bytes (94.8 MB) |
| Sampled combined app/browser peak RSS | 1,186,340,864 bytes (1.19 GB) |
| Service ready (`/api/config` responding) | 5.97 seconds |
| Hinton 24-hour baseline / wind variant | 14.13 / 15.99 seconds |
| Black Hawk 24-hour baseline / wind variant | 16.22 / 21.09 seconds |
| Managed user data after five test cases | 13,869,313 bytes (13.9 MB) |
| Cases recovered after forced termination | 5 |
| Interrupted checkpoint: observed / recovered | Hour 1 / hour 9 |
| Resumed horizon | Hour 96; first 25 frames exactly matched baseline |

The worker committed additional frames between the checkpoint observation and the
process kill; recovery retained those too. All four browser runs produced 25 frames,
with no script errors or non-loopback page requests. Times run from clicking Run
to observing completion, including the baseline's deliberate mid-run browser reload.
Service-ready time is not a separately measured browser-interactive startup time.
The full browser workflow passed, but its startup milestone is not independently timed.

Verified application-file-tree SHA-256:
`295e384bf68d5536c8a12e0d2e791840170765e824ee962271881bb370ca0ba1`.
The release script refuses to package a different tree using this acceptance record.

The local machine is Windows 11 x64, Intel Core Ultra 5 125U, with approximately
16 GB RAM. Browser automation uses a separate headless Chromium process. RSS sampling
includes service, calculation worker and browser trees (plus the browser harness);
shared pages may be counted more than once. This is not a clean installation on the
reference 8 GB laptop, a cold filesystem-cache benchmark or an OS-firewall test.
Other development processes were present; this was not an otherwise idle benchmark.

The acceptance targets remain startup under 30 seconds, a default 24-hour run under
120 seconds, and combined peak RAM under 4 GB on the reference 8 GB machine. These
are targets, not permission to silently reduce resolution or omit unsupported areas.

## Gates not yet satisfied

1. Execute the provided CI matrix and review its artifacts. The vetted pack ZIP and
   checksum are ready locally; a reviewed immutable build-input URL and repository
   runner access are still needed. Do not paste secrets into chat.
2. Hands-on macOS/Linux installations, including macOS 14 Intel compatibility,
   read-only application directories, cross-platform import/export and numerical
   equivalence. Windows Server CI is not a substitute for Windows 11 field checks.
3. Clean-install, host-network-blocked tests and complete browser/application
   resource measurements on representative 8 GB and 16 GB laptops.
4. Redistribution/license review and organizational review of the unsigned pilot;
   public code signing/notarization remain later distribution gates.
5. A fire-behavior/responder/community-planning reviewer and independent spread
   validation before any operational claims. Uncalibrated spread, historical inputs,
   unsupported urban/unknown cover and unmodeled general ember crossing remain
   visible in the interface and exports.

No additional private data or credentials are needed to try the local pilot.
Treatment effects, live forecasting, resource allocation, building-layout advice,
European validation and remote-community coverage audits remain outside this release.
