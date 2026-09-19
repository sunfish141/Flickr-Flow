"""Fire-state prediction independent of collection, training, and HTTP.

Load a fitted model, call initial_state(ignitions), then step(state,
terrain_provider=...) to predict the next 12-hour fire state.
"""

from wildfire_data.model.incident_transition import IncidentTransitionModel
from wildfire_data.model.loading import load_pass_model
from wildfire_data.model.recursive_transition import ActiveFireCell, RecursiveFireState
from wildfire_data.model.spread import FireSpreadModel

__all__ = ["ActiveFireCell", "FireSpreadModel", "IncidentTransitionModel", "RecursiveFireState", "load_pass_model"]
