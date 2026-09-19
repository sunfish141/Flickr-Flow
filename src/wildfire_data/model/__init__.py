"""Fire-state prediction independent of collection, training, and HTTP.

Load a fitted model, call initial_state(ignitions), then step(state,
terrain_provider=...) to predict the next 12-hour fire state.
"""

from importlib import import_module

__all__ = ["ActiveFireCell", "FireSpreadModel", "IncidentTransitionModel", "RecursiveFireState", "load_pass_model"]


def __getattr__(name):
    """Keep public research imports without loading ML for local planning."""
    modules = {'IncidentTransitionModel': 'incident_transition', 'load_pass_model': 'loading',
               'ActiveFireCell': 'recursive_transition', 'RecursiveFireState': 'recursive_transition',
               'FireSpreadModel': 'spread'}
    if name not in modules:
        raise AttributeError(name)
    value = getattr(import_module(f'wildfire_data.model.{modules[name]}'), name)
    globals()[name] = value
    return value
