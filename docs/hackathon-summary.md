**Wildfire Atlas: exploring how wildfires could spread**

Wildfire Atlas combines satellite fire detections, terrain, vegetation and road
data in an interactive map for exploring possible wildfire spread. It makes
complex geospatial models accessible through a simple workflow: place a fire
or load NASA FIRMS detections, run the simulation, and inspect the results.

The project provides two complementary simulation modes:

- **Regional machine learning:** a trained model estimates spread between
  1 km cells, advancing in 12-hour steps with vegetation-based fuel burnout
  and mapped water barriers.
- **Detailed polygon simulation:** fire travels through 30 m vegetation patches,
  with road geometry, fuel type and configured wind affecting its progression.
  The landscape expands as needed, with no fixed simulation duration cap.

Users can explore Colorado and Alberta, inspect local vegetation, replay earlier
frames, and compare simulations with historical satellite detections. Active
fire and burned areas remain distinct, and exhausted fuel cannot reignite.

The stack is **React and Leaflet, FastAPI, and Python geospatial and ML models**.
Prepared road and vegetation data stay in bounded memory caches. Incremental
spread calculations reuse previous work, making interactive playback practical
without loading an entire state or province into a detailed simulation.

The training pipeline uses a verified public release containing over 500,000
candidate examples, with separate training, calibration and evaluation groups.
Engineering validation includes **157 Python tests, 14 frontend tests, and a
real-data browser demonstration of seven days of polygon spread and exact replay**.

This is a research and education prototype. Detailed spread rates and fuel
durations remain assumptions; polygon wind is constant, and the coarse model
does not use weather. It is not validated for emergency decisions.
