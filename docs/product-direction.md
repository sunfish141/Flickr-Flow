# Rural wildfire planning and responder decision support

Direction clarified September 19, 2026. This records the intended product and
proposed implementation priorities, not newly delivered or validated capabilities.

## Intended users and decisions

Wildfire responders and community planners need to explore fire behavior before
an incident and understand possible consequences during one. Rural communities
are a priority, including places where a fire can interrupt electricity, internet
or access. The initial device target is a laptop with 8–16 GB RAM; phone work is
deferred. Classifier weights do not need to approach the 15–20 GB allowance.

Two workflows should share local data packs but have distinct input/output contracts:

| Workflow | Starting information | Useful comparison | Important boundary |
| --- | --- | --- | --- |
| Preparedness and community planning | Hypothetical ignition, explicit weather/moisture scenarios, mapped terrain/fuels and proposed interventions | Baseline versus changed landscape/access, evaluated under the same scenario set | Conditional scenario outcomes are not annual fire probabilities or proof a design is safe |
| Incident decision support | Timestamped observations or responder-entered perimeter, cached issued forecasts, resource/access information | Plausible spread envelopes, exposed assets, access constraints and alternative staging assumptions | No automatic dispatch, safe-route certification or tactical recommendation from the current research model |

Responder resource allocation is an eventual use case, not evidence that the
current prototype is suitable for operational decisions. It requires validation
with fire-behavior specialists and local agencies. Keep the current research
limitations visible while building toward this goal.

## Geography: impact plus usable evidence

Prioritize consequences to communities, repeated exposure, limited access,
communications vulnerability, suitable input coverage and a partner able to
evaluate results. Burned hectares alone are not a sufficient ranking.

The supplied incident-assignment metadata contains 610 unique `BorealNA` and
399 unique `CONUS` source-incident keys. These are source keys, not counts of
independent training incidents or country-level counts. No European incident
family was found in that manifest. The canonical model grid is North American
Albers (`ESRI:102008`), and the configured detailed landscape pilots/presets are
Alberta and Colorado. European training/deployment coverage is not established
by having a global basemap or global weather access.

Proposed sequence, not a claimed global ranking of wildfire impact:

1. Use the existing Alberta foothills and Colorado landscapes for engineering
   and benchmark scenarios. They minimize new data plumbing.
2. Include a remote/rural boreal Canadian evaluation area, chosen after a local
   coverage audit and preferably with a community or response partner. Northern
   Manitoba/Saskatchewan or a suitable northern Alberta/BC area are candidates,
   not yet selected deployments. Do not assume one foothills pilot represents
   remote communities.
3. Treat a Mediterranean European pilot as a separate transfer-validation task,
   with appropriate fuels, terrain, projection, weather and incident labels.
   Portugal, Spain and Greece are candidates, not assumed supported regions.

Canada's documented 2024 evacuations include remote communities, supporting
the rural focus. European fire reporting also provides long-running southern
European comparisons that could inform later site selection.
[NRCan annual report](https://natural-resources.canada.ca/forests-forestry/state-canada-forests/state-canada-s-forests-annual-report-2025),
[European fire report](https://op.europa.eu/en/publication-detail/-/publication/1f960275-d187-11f0-8da2-01aa75ed71a1/language-en).

## Implications for the model

The current boosted classifier estimates a satellite-dependent 12-hour frontier
transition. Its inputs depend on an existing fire pattern. Better observed-row
AP does not establish ignition risk in an unburned community, accurate multi-step
spread, or the causal benefit of changing roads, fuels or building layouts.
The recursive renderer supplies synthetic FIRMS-like states; that is a separate
assumption requiring rollout validation.

For planning, compare a compact, interpretable fire-behavior baseline with a
hybrid using learned corrections. Keep boosted trees as a baseline and potential
correction component. Before attempting learned corrections, define physically
meaningful state/targets, obtain suitable fuel/moisture inputs and establish
perimeter/arrival-time benchmarks. Do not make architectural complexity the goal.

Established FlamMap/FARSITE workflows provide useful external benchmarks for
conditional scenarios and time-varying growth. They require fire-behavior fuel
and canopy inputs beyond a broad land-cover class. Their existence does not
validate our implementation or imply their full workloads meet our RAM budget.
[US Forest Service documentation](https://research.fs.usda.gov/firelab/products/dataandtools/flammap).

The existing local engine is explicitly heuristic: assumed vegetation-group
travel rates, constant wind, a 30/100 m mesh and limited optional road-crossing
spotting. Configured spotting is currently zero. Urban mixtures and structures
are unsupported, not nonflammable. A road appearing to stop a simulated fire
must not be interpreted as evidence that a road is a reliable firebreak. Do not
choose city layouts or quantify building protection using this model alone.

Run paired baseline/intervention scenarios across the same ignition, wind and
moisture cases. Save assumptions and random seeds. Report scenario sensitivity
and uncertain coverage; the fraction of hand-picked scenarios reaching a site
is not a calibrated probability. Include severe plausible cases, not only a
single visually convincing animation.

## Additional data priorities

1. Moisture history and issued weather, continuing the bounded source pilot.
   Retrospective analysis, forecast initialization, publication and download
   times must remain distinguishable.
2. Region-appropriate fire-behavior fuel types and terrain. Broad land cover is
   not a complete fuel model; building/ember vulnerability needs separate data.
3. Community exposure and access: roads with verified constraints, settlements
   or building footprints, population aggregates, water supplies and critical
   facilities. Local agency/community GIS should supplement public maps.
4. For operational allocation, current resource locations/capabilities, verified
   closures, availability and communications status. These are not supplied by
   a fire-spread model, and a missing record must not mean an open road or an
   available crew.

Keep exposure/access layers separate from physical spread inputs. They explain
consequences and logistics, not why fire propagates. Public map completeness
and coarse population grids cannot certify a route or count everyone at risk.
Do not collect or expose sensitive responder locations in public artifacts.

## Offline and power-loss requirements

The requirement is disconnected startup and recovery, not merely continuing
to calculate after losing a connection:

- Install models/runtime and download bounded terrain/fuel/map packs in advance.
  No required login, cloud inference, tile request or startup download offline.
- Save scenarios durably and checkpoint progress. Current frontend scenarios
  exist only in tab memory and are lost on reload.
- Support explicit hypothetical weather and manual ignition/perimeter entry
  without a live satellite feed. Label assumptions separately from observations.
- Retain source timestamps, coverage and model versions. Missing or stale
  weather/observations must be visible; do not silently reuse them as current.
- Offer bounded-area, low-compute runs; checkpoint and resume after shutdown.
  Battery/runtime and complete-app RAM measurements are required on the laptop.
- Export a self-contained scenario summary/map with assumptions and timestamps
  for handoff when connectivity is absent. Local copies and a paper fallback
  complement, but cannot replace, available power and agency communications.

## Next implementation priorities

First deliver durable, reproducible offline scenarios and explicit data-status
reporting. Then establish paired scenario comparisons and validate the local
engine's response to wind, moisture, fuel changes and ember crossing. Continue
the modest moisture backfill in parallel as a separate predictive experiment.

Evaluation must extend beyond average precision: independent perimeter/growth
comparisons, arrival-time error where observations support it, calibration and
scenario sensitivity, missed exposed assets, runtime/RAM, disconnected cold
start and restart recovery. Suppression/resource-allocation effects require
their own evidence and expert review.

No exact town, phone choice or larger hardware budget is needed to start those
engineering tasks. A responder, fire-behavior analyst or community-planning
partner is the most useful next external input. Geography-specific deployment
claims wait for the coverage audit and that partner's evaluation.
