"""Compatibility for estimator references in pre-reorganization joblib bundles.

New code imports wildfire_data.model.incident_transition.
"""

from wildfire_data.model.incident_transition import CalibratedSpreadEstimator

__all__ = ["CalibratedSpreadEstimator"]
