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

The runtime and CSV-training milestones are complete. The model run uses the
real public release; **74 Python tests pass**, saved-model evaluation reproduces
all recorded scores, and real-model API replay is deterministic. See
[model results](model-results.md), [architecture](architecture.md), and
[feature availability](feature-map.md). The browser-state milestone is next.

Each milestone is tested before commit; the user syncs it at the checkpoint. Existing training data,
license, configurations, and the original application repository are preserved.
