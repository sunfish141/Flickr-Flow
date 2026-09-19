# Implementation notes

The application is being rebuilt incrementally from `recreate-project-prompt.md`.

1. Standalone runtime: retain the tested spatial/prediction algorithms and
   React views, separate FastAPI settings/resources/routes, relocate local
   source adapters into `providers/`, and exclude bulk collectors.
2. Public CSV training: verify `htn_training/`, join by exact example identity,
   fit/calibrate the frontier model and independent offline weather policy,
   and record measured held-out results with portable artifacts.
3. Browser state: isolate timeline transitions and request invalidation, retain
   all map and source controls, and verify interactions with browser tests.

All three implementation milestones are complete. The model run uses the
real public release; **137 Python tests and 14 frontend tests pass**, saved-model
evaluation reproduces all recorded scores, and real-model API replay is
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
