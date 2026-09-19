# Wildfire Planner: internal Windows pilot

This is an unsigned, research-only sensitivity model, not an operational wildfire
forecast or a tool for deciding where responders should deploy. Its spread rates
are uncalibrated. Urban/unknown areas are unsupported, not safe. Ember crossing
is not modeled by the default scenario policy.

## Start

1. Extract the complete ZIP to a folder you can keep. Do not run from inside the ZIP.
2. Open `WildfirePlanner/WildfirePlanner.exe`. Keep its `_internal` folder beside it.
3. Your installed browser opens the local application. No Python, Node, account,
   credentials or internet connection is required. Do not run as administrator.

The application is unsigned. Windows security software may flag it; do not disable
your security controls. Use your organization's review process if it blocks launch.
The console window belongs to the local service; closing it stops the application.
Closing a browser tab alone does not stop a running calculation.

## Try a comparison

1. Choose Hinton or Black Hawk and select **New baseline**.
2. Name the case, select its UTC start time and add an ignition by map click or
   coordinates. Ignitions must be inside supported vegetation in the prepared pack.
3. Choose wind speed in metres per second and meteorological **wind FROM** degrees:
   0 is from north, 90 from east, 180 from south, and 270 from west.
4. Wait for **Saved**, then run the scenario. Defaults are a 100 m mesh, a 24-hour
   horizon and hourly output. Read the source dates and coverage warnings.
5. Select **Clone as variant**, change the ignition or wind, and run again. Choose
   the baseline as the comparison case to use synchronized side-by-side playback.

Computed cases cannot be edited in place. Clone them to preserve the original
results. Pause retains the last committed frame; reopening the app restores saved
cases with playback paused. An interrupted run can resume when its exact pack and
engine are still installed. An unsaved or storage-error message means the last
edit has not been acknowledged: do not assume that edit survived a shutdown.

## Keep and share results

- **Export JSON** retains the definition, provenance, map snapshot and saved frames.
  Import this format into the planner. Imported results are marked unverified and
  view-only; clone and rerun against matching trusted resources for a new calculation.
- **Export HTML** produces a standalone offline report with a map and assumptions.
- **Export GEOJSON** produces geographic results for GIS tools.

Cases are stored in the operating system's user-data directory, not the browser
or extracted application folder. On Windows this is normally
`%LOCALAPPDATA%\WildfirePlanner`. Do not manually edit a running database. Export
important cases before moving computers or upgrading. Missing or changed packs
block continuation but do not erase retained results. The app never automatically
deletes saved cases to meet its 20 GB managed-storage cap.

Only the two 9 × 9 km pilot regions are included. Historical land cover, incomplete
road attributes and modeled boundary stops limit what a comparison means. A smaller
modeled burned area is not evidence that a treatment or development plan is safer.

See `VERIFICATION.md` in the handoff for measured Windows checks and remaining
cross-platform, reference-device, offline-installation and scientific review gates.
