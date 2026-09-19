"""Past-only detection geometry and wind relative to candidate cells."""

from datetime import timedelta
import math

import numpy as np
from pyproj import Geod

from wildfire_data.core.grid import cell_from_id, cell_from_wgs84
from wildfire_data.model.features.fire_state_features import _parse_evidence


GEOD = Geod(ellps="WGS84")
GEOMETRY_COLUMNS = (
    "fire_5x5_detection_count", "fire_5x5_active_cell_count", "fire_5x5_recent_12h_count",
    "fire_nearest_detection_km", "fire_nearest_cell_km", "fire_nearest_detection_age_hours",
    "fire_distance_weighted_detection_count", "fire_recent_fraction",
)
WIND_GEOMETRY_COLUMNS = ("wind_from_nearest_fire_m_s", "wind_from_fire_weighted_m_s", "wind_nearest_alignment")
DIRECTIONAL_COLUMNS = GEOMETRY_COLUMNS + WIND_GEOMETRY_COLUMNS


def detection_index(records):
    indexed, seen = {}, {}
    for record in records:
        evidence = _parse_evidence(record)
        previous = seen.get(evidence.detection_id)
        if previous is not None:
            if previous.fingerprint != evidence.fingerprint:
                raise ValueError("conflicting FIRMS detection identity")
            continue
        seen[evidence.detection_id] = evidence
        cell = cell_from_wgs84(latitude=evidence.latitude, longitude=evidence.longitude)
        indexed.setdefault((cell.x_index, cell.y_index), []).append(evidence)
    return indexed


def directional_features(cell_id, cutoff, indexed, *, wind_u, wind_v):
    cell = cell_from_id(cell_id)
    lower, upper = cutoff - timedelta(hours=24), cutoff - timedelta(hours=3)
    detections = []
    cells, local_count = set(), 0
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            key = cell.x_index + dx, cell.y_index + dy
            for evidence in indexed.get(key, ()):
                if lower <= evidence.acquired_at <= upper:
                    detections.append((evidence, math.hypot(dx, dy)))
                    cells.add(key)
                    local_count += abs(dx) <= 1 and abs(dy) <= 1
    result = dict.fromkeys(DIRECTIONAL_COLUMNS, float("nan"))
    result.update(fire_5x5_detection_count=len(detections), fire_5x5_active_cell_count=len(cells),
                  fire_5x5_recent_12h_count=0, fire_distance_weighted_detection_count=0.)
    if not detections:
        return result, local_count
    detections.sort(key=lambda item: item[0].detection_id)
    latitude, longitude = cell.center_wgs84
    bearings, _, distances = GEOD.inv(
        [d.longitude for d, _ in detections], [d.latitude for d, _ in detections],
        [longitude] * len(detections), [latitude] * len(detections))
    distances = np.asarray(distances) / 1000.
    ages = np.asarray([(cutoff - d.acquired_at).total_seconds() / 3600. for d, _ in detections])
    nearest = int(np.argmin(distances))
    bearings = np.radians(bearings)
    toward = wind_u * np.sin(bearings) + wind_v * np.cos(bearings)
    # At zero separation a direction is undefined; use a zero directional component.
    toward = np.where(distances > 1e-8, toward, 0.)
    weights = 1 / (.5 + distances) ** 2
    speed = math.hypot(wind_u, wind_v)
    recent = int((ages <= 12).sum())
    result.update(
        fire_5x5_recent_12h_count=recent,
        fire_nearest_detection_km=float(distances[nearest]),
        fire_nearest_cell_km=min(d for _, d in detections),
        fire_nearest_detection_age_hours=float(ages[nearest]),
        fire_distance_weighted_detection_count=float(weights.sum()),
        fire_recent_fraction=recent / len(detections),
        wind_from_nearest_fire_m_s=float(toward[nearest]),
        wind_from_fire_weighted_m_s=float(np.average(toward, weights=weights)),
        wind_nearest_alignment=float(toward[nearest] / speed) if speed else 0.)
    return result, local_count
