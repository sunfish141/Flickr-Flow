"""Convert model states and predictions into inspectable map responses."""

from dataclasses import asdict
from datetime import timedelta

from wildfire_data.core.grid import cell_from_id
from wildfire_data.model.incident_transition import EvidenceCell


def state_response(state, *, origin_at, predictions=(), metadata=None, terrain_missing=0):
    active = {c.cell_id: c for c in state.active_cells}
    burned = set(state.burned_cell_ids)
    points = []
    scores = {p.cell_id: p for p in predictions}
    for cell_id in sorted(set(active) | burned | set(scores)):
        lat, lon = cell_from_id(cell_id).center_wgs84
        cell, score = active.get(cell_id), scores.get(cell_id)
        status = "active" if cell else "burned" if cell_id in burned else "candidate"
        points.append({"cell_id": cell_id, "latitude": lat, "longitude": lon, "status": status,
            "intensity": cell.intensity if cell else None,
            "fuel_remaining": cell.fuel_remaining if cell else None,
            "burn_duration_hours": getattr(cell, 'burn_duration_hours', None),
            "fuel_basis": getattr(cell, 'fuel_basis', None),
            "vegetation_fraction": getattr(cell, 'vegetation_fraction', None),
            "ignition_probability": score.ignition_probability if score else None,
            "new_ignition": bool(score and score.will_ignite),
            "source": "FIRMS observation" if isinstance(cell, EvidenceCell) else "Placed ignition" if cell and state.step_index == 0 else "Simulation",
            "observation_age_hours": cell.observation_age_hours if cell else None,
            "detection_count": cell.detection_count if isinstance(cell, EvidenceCell) else None,
            "bright_ti4_max": cell.bright_ti4_max if isinstance(cell, EvidenceCell) else None,
            "remaining_active_steps": cell.remaining_active_steps if cell else None})
    return {"state": asdict(state), "origin_at": origin_at.isoformat(),
        "valid_at": (origin_at + timedelta(hours=12 * state.step_index)).isoformat(),
        "elapsed_hours": 12 * state.step_index, "points": points,
        "active_count": len(active), "burned_count": len(state.burned_cell_ids),
        "new_ignition_count": sum(p.will_ignite for p in predictions),
        "finished": False, "extinct": not active,
        "terrain_missing_count": terrain_missing, "metadata": metadata}
