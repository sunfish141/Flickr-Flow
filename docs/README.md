# Implementation notes

The application is being rebuilt incrementally from `recreate-project-prompt.md`.

For a concise overview of the data sources, learned-model architecture and
polygon algorithm, see the [hackathon project summary](hackathon-summary.md).
The [weather ML polygon integration](weather-polygon.md) explains the optional
hybrid mode, historical/live weather sources and its experimental assumptions.

1. Standalone runtime: retain the tested spatial/prediction algorithms and
   React views, separate FastAPI settings/resources/routes, relocate local
   source adapters into `providers/`, and exclude bulk collectors.
2. Public CSV training: verify `htn_training/`, join by exact example identity,
   fit/calibrate the frontier model and independent offline weather policy,
   and record measured held-out results with portable artifacts.
3. Browser state: isolate timeline transitions and request invalidation, retain
   all map and source controls, and verify interactions with browser tests.

All three implementation milestones are complete. The model run uses the
real public release; saved-model evaluation reproduces all recorded scores,
model/API/browser regression checks pass, and real-model API replay is
deterministic. Production-browser checks cover request cancellation, historical
replay, keyboard controls, mobile layout and automated accessibility. See
[model results](model-results.md), [architecture](architecture.md), and
[feature availability](feature-map.md). [Verification notes](verification.md)
distinguish real inference from explicit browser fixtures and list remaining
resource requirements.

The handoff also includes [provider contracts and configuration](providers.md)
and [research limitations and missing inputs](limitations.md). These describe
what a code-only checkout needs before it can reproduce the locally tested run.

[Startup vegetation and polygon restoration](startup-data.md) extends the
initial reconstruction: automatic source preparation now restores measured
vegetation, fixed polygon pilots and expanding road-aware fuel patches. These
capabilities passed real-data API and browser checks in this repository. Colorado
and Alberta now replace the small pilot choices with full-region map views and
on-demand polygon simulation using the retained national sources.

Each milestone is tested before commit; the user syncs it at the checkpoint. Existing training data,
license, configurations, and the original application repository are preserved.

[Fuel duration and water barriers](fuel-and-water.md) documents vegetation-based
burnout, its fallback assumptions, and the native water mask that prevents
coarse spread over Athabasca River cells missed by the generalized map.

Polygon playback now has no duration cap. Arrival searches resume only through
the requested time, while spatial budgets and historical date limits still
apply. See [feature availability](feature-map.md) and
[verification](verification.md#polygon-playback-without-a-duration-cap).
