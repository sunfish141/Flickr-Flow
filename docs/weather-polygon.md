# Weather ML and polygon spread

Select an expanding polygon region, then enable **Weather ML + polygon
(experimental)**. A manually placed fire uses a captured forecast by default.
To use historical weather without a satellite archive, set **Historical weather
date** before placement (May 11–August 21, 2026). Historical FIRMS scenarios also
select historical weather automatically when the satellite archive is available.
Changing the option or placement date resets the scenario.

This integrates the existing trained weather policy into polygon propagation.
It is an experimental coupling; its perimeter accuracy has not been calibrated
or independently evaluated. Standard polygon mode and the coarse ML engine
remain separately selectable.

## How the models interact

1. At each 12-hour boundary, aggregate currently burning patches into 1 km cells.
   Convert their maximum remaining fuel fraction into explicit synthetic fire
   observations using the retained training-only brightness/count renderer.
   These proxies use cell centres and a 7.5-hour observation age; they are not
   predicted satellite measurements or new satellite observations.
2. Build the same 3×3 fire/terrain, 5×5 fire geometry, directional wind and
   six-hour weather features expected by the trained `WeatherPolicy`.
   New-cell candidates lie within two cells of the active state. Previously
   entered cells continue their detailed spread without another admission draw.
3. Batch-score the candidate cells with the saved calibrated boosted trees:
   `p = 0.75 × geometry_probability + 0.25 × weather_probability`.
   A new cell is eligible when `p >= 0.15` and a stable cell/window draw is below
   `p`. One decision applies to that entire 1 km cell for the window.
4. Run the native patch graph inside that constraint. A high score cannot ignite
   an isolated patch or bypass a road/water gap. Probabilities do not become
   metres-per-minute speeds. Configured fuel-specific speeds remain in effect,
   with directional wind from the captured weather at the window's start.
5. Each patch keeps its original arrival and burnout time. A rejected cell can
   be reconsidered next window only if an adjoining source remains alive long
   enough to wait and reach its exit. Exhausted fuel cannot reignite.

Accepted edge travel retains the wind used at departure, including travel that
finishes across a window boundary. The solver does not search for a beneficial
future waiting time. Spotting must be disabled in this first integration.
Terrain enters the coarse ML score; the native travel model still assumes flat
terrain. Missing terrain outside retained coverage remains NaN in the model inputs.
The trained observed-row feature distribution differs from these
synthetic rollouts, so the original classifier's scores do not validate this
combined simulator.

## Weather sources and time handling

Forecasts use the [Open-Meteo ECMWF API](https://open-meteo.com/en/docs/ecmwf-api),
requesting `ecmwf_ifs`, 15 forecast days and two past days. Historical scenarios
use [historical IFS analysis](https://open-meteo.com/en/docs/historical-weather-api)
through August 22, matching the training weather family. Historical analysis is
retrospective evidence, not a forecast proven available at the historical origin.

Both request hourly temperature, relative humidity, precipitation, wind speed
and direction in Celsius, percent, millimetres and metres per second. Wind
direction is converted from meteorological "from" bearing into east/north
components. Each history window uses exactly the anchor hour and previous five
hours, without future values. Missing hours, nulls, wrong units and invalid
values reject the request; unavailable weather does not become calm/dry weather.

The first geographic ignition selects one representative weather location,
rounded to three decimal places. That weather applies to the entire scenario;
the UI states this spatial approximation. It is not a 30 m weather forecast.
The location and dates go to Open-Meteo through the app server.

After the forecast ends, playback can continue with the last available hourly
conditions held constant, including precipitation. The frame and UI explicitly
label this as an assumption beyond the forecast. The first step straddling the
forecast end is already marked as an assumption; that step retains its
window-start weather before later windows use the last row. Historical playback
stops before the next full step would exceed captured analysis coverage;
satellite comparison also retains its existing observation-date limit.

## Caching and identity

The trained weather artifact loads once through its verified public-run manifest.
Weather snapshots are captured at scenario creation and never refreshed during
replay. They persist as checksummed JSON under `data/runtime/scenario-weather/`;
the request state contains only their digest. Origin, weather digest, model
artifact and landscape policy participate in scenario identity. A missing or
changed snapshot requires a new scenario rather than silently substituting data.

Repeated placements at the same rounded location/hour reuse a capture. The
provider retains 32 snapshots in memory and caps its disk cache at 256 MB;
each snapshot is at most 512 KB. Each prepared landscape model retains at most
two hybrid searches. Existing tile, patch and assembled-model limits still
apply. New tiles reuse cached geometry; their joined search is recomputed from
the original ignitions against the same pinned weather.

## Verification and implementation

Provider tests cover units, wind direction, six-hour history, missing evidence,
immutable replay and forecast extrapolation. Engine tests isolate ML admission
from physical travel and preserve road/water barriers, burnout, retry and replay.
API tests cover both sources, manual historical placement, expansion, identity,
cache eviction and historical stopping. The real-data browser check is:

```bash
python tests/web/browser_weather_polygon.py http://127.0.0.1:8000
```

It uses the real trained model, native landscapes and both weather endpoints
in Colorado and Alberta. Only basemap images are stubbed. Local reports and
screenshots are stored in `artifacts/weather-polygon/browser/`.

Code: [weather provider](../src/wildfire_data/providers/scenario_weather.py),
[hybrid propagation](../src/wildfire_data/model/weather_hybrid.py),
[model resource loader](../src/wildfire_data/web/weather_landscape.py).
