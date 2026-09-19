# Reconstruction verification

Validated September 19, 2026 in this repository using Python 3.14.4, Node 24.18.1,
Playwright 1.62.0 and its Chromium browser. The production bundle was rebuilt from
the committed React sources.

| Check | Result |
| --- | --- |
| Python behavior/regression suite | 117 tests passed |
| Frontend API, timeline and cancellation unit tests | 11 tests passed |
| Production Chromium interactions | Passed |
| Automated axe WCAG A/AA checks | No violations in five tested states |
| Browser JavaScript errors / CSP violations | None |

The browser suite starts with real API placement and 12-hour predictions through
144 simulated hours using the newly fitted CSV frontier model. It checks
keyboard placement, inspector focus, pausing in flight and resuming, and then
uses explicit API fixtures to test 2,000-cell display pagination, escaped API
text, the rolling 128-frame window, reset during inference, an overlapping
canceled request and replacement seed, hiding during initialization, source
changes, provider failure and retry cooldown.

Historical fixtures verify fixed comparison bounds, complete state requests,
24-hour advancement, the observation inspector and layer control, replay without
additional inference requests, and automatic stopping at the final day.
Historical archive loading and two-step simulation also have Python API tests
against generated source archives. Browser fixtures do **not** establish that
the real retained archive or live NASA connection is available on this server.

Basemap tiles are stubbed before navigation. Initial request checks find only
the app origin and the expected tile-provider URL. Accessibility audits cover
desktop initial state, desktop inspector, help dialog, and an open inspector at
390 px and 320 px widths. They are automated checks, not a complete manual
assistive-technology assessment. Screenshots and JSON reports are generated
locally under `artifacts/web-react-preview/` and excluded from Git.

Handoff checks also confirmed the training CLI help, consistent dependencies
via `pip check` in the tested environment, and valid local documentation links.
An app started with explicitly missing model/data paths still served its UI and
assets, reported unavailable capabilities, and returned a sanitized 503 for
simulation. The four focused FIRMS tests passed after aligning the missing-key
message with environment-based configuration. This was not a fresh dependency
installation or a live NASA connectivity check.

The later vegetation/polygon restoration passed real-source API and Chromium
checks for canopy inspection, road geometry, perimeters, expansion and replay.
Startup also passed without the old repository or downloads after restoration.
See [startup data verification](startup-data.md#verification) for measurements.

Colorado and Alberta regional checks also passed real-source polygon seeds and
12-hour expansion outside the old Boulder/Edson pilots. Each grew from one tile
to two. Browser checks verified both views and coordinate examples; isolated
FIRMS probes verified viewport bounds and regional selection across live and
historical controls without contacting NASA. Reports and frames are under
`artifacts/regional-landscape/browser/`; see the
[regional measurements](startup-data.md#colorado-and-alberta-verification).

## Repeat the checks

Install the Python dependencies from `requirements.lock` and run `npm ci` in
`frontend/`. Run from the repository root:

```bash
PYTHONPATH=src OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  python -m unittest discover -s tests
node --test frontend/tests/*.test.js
node frontend/build.mjs
```

For browser verification, install the test browser and its system dependencies:

```bash
python -m pip install playwright==1.62.0
python -m playwright install --with-deps chromium
PYTHONPATH=src OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  python -m uvicorn wildfire_data.web.app:app --host 127.0.0.1 --port 8001
```

Keep that server running and, in another terminal, run:

```bash
python tests/web/browser_app.py http://127.0.0.1:8001
python tests/web/browser_restored_landscape.py http://127.0.0.1:8001
python tests/web/browser_regional_landscape.py http://127.0.0.1:8001
```

The browser suite requires a ready model. Train the public-CSV model first using
the root README commands, or explicitly configure trusted compatible artifacts.
Startup now restores or prepares vegetation and road sources; the local copy
has passed real-data checks. Real historical comparisons still require their
retained source archive. Models and source data are ignored artifacts; syncing
code alone does not transfer them.
