# Web model recovery — 2026-09-19

The original web interface reported `model_ready: false` because neither its
legacy fitted bundle nor `artifacts/public-csv/run_manifest.json` existed locally.
The offline planner's local travel engine is separate and was not changed.

Rebuilt the documented, fixed 300-iteration reference models from the original
checksum-verified CSV release using `.venv-web` and `requirements.lock`. This is
a new reference fit, not recovered legacy weights and not promotion of an
experimental model. The reference trainer now also excludes development labels
ending at or after the later-time test boundary: seven training and two calibration
rows were excluded, leaving 20,933 and 3,401 rows respectively. The protocol records
these exclusions. Original CSVs and earlier experiment results were not modified.

Artifacts, source provenance, dependency versions, held-out evaluation, reload
parity and artifact checksums are under `artifacts/public-csv/`. The map loads the
frontier classifier; the separately fitted historical-weather component is not
automatically used as live or recursive forecast weather.

Verification: nine public-CSV model/data tests passed. After restarting the server,
`GET /api/config` reported `model_ready: true` and `Frontier CSV model`. Both ignition
creation and a real 12-hour simulation step returned HTTP 200. A supported test
neighborhood at 58.947698, -113.623403 scored eight candidates with zero missing
terrain cells and produced three modeled ignitions. An out-of-coverage-neighbor
test retained its missing-terrain warning. These are functionality checks, not
operational validation.

To restart the web preview from the repository root in PowerShell:

```powershell
$env:PYTHONPATH = 'src'
$env:OMP_NUM_THREADS = '2'
$env:OPENBLAS_NUM_THREADS = '2'
$env:WILDFIRE_PREPARE_DATA = '0'
$env:WILDFIRE_DOWNLOAD_VEGETATION = '0'
$env:WILDFIRE_RUN_MANIFEST = (Resolve-Path artifacts/public-csv/run_manifest.json).Path
.venv-web\Scripts\python -m uvicorn wildfire_data.web.app:app --host 127.0.0.1 --port 8000
```

Use the matching web environment for these fitted artifacts. The completed public
CSV manifest is also auto-detected by the normal web settings. Refresh the browser
after restarting. Bulk preparation remains disabled for this preview. Live FIRMS,
the historical archive and legacy detailed-landscape resources remain separately
unconfigured/unavailable; restoring this classifier does not supply those inputs.
