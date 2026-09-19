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

Each milestone is tested before commit and push. Existing training data,
license, configurations, and the original application repository are preserved.
