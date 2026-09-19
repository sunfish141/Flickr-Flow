# Fuel duration and water barriers

The interactive models use vegetation to estimate fuel duration. These rules
are explicit scenario assumptions, not a fitted fuel-consumption model or a
measurement of biomass, dead wood, litter, duff or fuel moisture. The learned
frontier classifier and its training/evaluation artifacts are unchanged.

## One-kilometre fuel clock

`config/fuel_policy.json` assigns full-cover durations:

| Vegetation | Full-cover duration |
| --- | --- |
| Needleleaf forest | 72 hours |
| Broadleaf forest | 48 hours |
| Mixed forest | 60 hours |
| Shrubland, wetland, other vegetation | 24 hours |
| Grassland, cropland | 12 hours |

For land-cover evidence, multiply each duration by its mapped fraction of the
whole cell, then add those contributions. Where quality-supported canopy cover
is available, a lower observed tree-plus-other-vegetation fraction scales down
the vegetated contribution. Unmapped land-cover area contributes the 24-hour
fallback in proportion to its area; it is not treated as measured bare land.
If only canopy is available, tree cover uses the mixed-forest duration and
non-tree cover uses the grassland duration. If neither is available, assign
the explicit 24-hour fallback. Known zero supported fuel does not ignite.

Selection uses the scenario origin and the inspector's existing source/quality
policy, including explicitly identified retrospective static context. Advancing
time does not admit a newer composite. The assigned duration, vegetation fraction
and evidence basis travel with each active cell in the request/response state.
Satellite seeds preserve their observation aggregates and receive the same fuel
rule as placed and subsequently ignited cells. Source observations do not prove
when a real fire began; satellite seed fuel is initialized at scenario start.

Each 12-hour step consumes `12 / assigned_duration_hours` of that cell's initial
unit fuel budget. Burnout depends on fuel exhaustion, rather than the brightness
proxy or a fixed two-step countdown. Durations below 12 hours appear burned at
the next frame; longer durations are rounded up to the next display step.
Historical days run two such steps. Burned cells cannot reignite. The active-cell
inspector shows the assigned duration and its evidence basis; reference vegetation
measurements remain unchanged as simulated fuel is consumed.

## Polygon fuel clock

`config/local_spread.json` supplies `policy.residence_minutes_by_fuel`: needleleaf
240, broadleaf 180, mixed forest 210, shrubland/wetland/other vegetation 120,
and grassland/cropland 60 minutes. Each patch's clock starts at its own arrival
time. The same duration also determines whether its front can reach a shared
gate before the source patch burns out. Native land-cover classes identify
fuel; coarse canopy measurements are not downscaled into invented 30 m values.
Patch area controls burned/active area totals, not an inferred fuel depth.
Configurations without the class map retain their explicit uniform
`residence_minutes` value. Model v5 changes the scenario identity; start a new
polygon scenario after upgrading.

Normal polygon playback has no duration cap. Advancing beyond 96 hours keeps
each patch's original arrival and burnout times; exhausted fuel never regrows.
The clock can continue after all reachable fuel has burned out.

## Water exclusion

The bundled Natural Earth coastline/lake layer missed Athabasca River cells
at Fort McMurray and some Colorado reservoirs. The normal runtime now supplements
it with the retained 30 m NALCMS water classification. Any measured water area
excludes the entire 1 km cell; this deliberately excludes mixed shoreline cells.
Diagonal spread also requires both intervening orthogonal cells to be eligible,
so a front cannot cut between blocked water cells. Original geometric path
checks remain in force. This is a coarse barrier, not a water-crossing or ember
transport model. Water narrower than the source resolution can remain unresolved.

The same mask applies to placed seeds, live/historical satellite seeds, active
state and spread candidates. Submitted burned water cells are removed as well.
Unknown raster support falls back to the bundled geometry, not a fabricated
water measurement. `/api/config` reports which barrier is active. Water uses
current retained geography even for historical scenarios; fuel selection keeps
its separate origin cutoff. Neither operation fetches remote data during spread.

Water and fuel share the inspector's bounded native-cell cache. Fuel estimates
have a bounded origin-keyed cache; active-cell durations persist in browser state.
Normal startup primes regional examples so opening source readers does not fall
on the first fire request. Repeated steps reuse those measurements.

The [CEC land-cover product](https://www.cec.org/north-american-environmental-atlas/land-cover-30m-2020/)
provides mapped classes, including water; it is not a fuel-mass product. The
[US Forest Service burning-rate overview](https://research.fs.usda.gov/firelab/projects/burningrate)
describes additional fuel-bed and moisture influences on burning duration. The
numeric duration settings above are this app's assumptions, not published values
from either source.
