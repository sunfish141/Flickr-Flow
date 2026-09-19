"""Load checksum-verified model bundles for prediction."""

import json
from pathlib import Path

import joblib

from wildfire_data.core.hashing import sha256_file
from wildfire_data.core.model_artifacts import PUBLIC_MODEL_KIND, public_artifact
from wildfire_data.model.incident_transition import INCIDENT_TRANSITION_VERSION, IncidentTransitionModel
from wildfire_data.model.recursive_transition import RECURSIVE_MODEL_FEATURE_COLUMNS, SyntheticObservationCalibration
from wildfire_data.model.features.schema import feature_set_for_columns, validate_feature_contract
from wildfire_data.model.features.vegetation_features import VegetationFeatureSampler


def load_pass_model(run_manifest_path: Path, pass_name="pass_2"):
    """Load a trusted local fitted bundle only through its completed run manifest."""
    manifest = json.loads(run_manifest_path.read_text())
    if manifest.get('kind') == PUBLIC_MODEL_KIND:
        path, manifest = public_artifact(run_manifest_path, 'frontier.joblib')
        bundle = joblib.load(path)
        features = tuple(bundle['feature_columns'])
        if features != tuple(RECURSIVE_MODEL_FEATURE_COLUMNS) or list(features) != manifest['frontier_features']:
            raise ValueError('Public CSV frontier feature contract mismatch')
        if tuple(bundle['model'].columns) != features:
            raise ValueError('Estimator feature order differs from the model contract')
        return IncidentTransitionModel(bundle['model'], feature_columns=features,
            observation_calibration=SyntheticObservationCalibration(**bundle['observation_calibration']),
            ignition_threshold=bundle['ignition_threshold'])
    if manifest.get("kind") != "completed-incident-two-pass-training-run" or manifest.get("status") != "complete":
        raise ValueError("model loading requires a completed two-pass run")
    if pass_name not in ("pass_1", "pass_2"):
        raise ValueError("pass_name must be pass_1 or pass_2")
    artifact = manifest["artifacts"][pass_name]
    artifact_path = Path(artifact['path'])
    if not artifact_path.is_absolute():
        # Bundles distributed with a run remain usable after relocating it.
        sibling = run_manifest_path.parent / artifact_path.name
        if sibling.is_file():
            artifact_path = sibling
    if sha256_file(artifact_path) != artifact["sha256"]:
        raise ValueError("model bundle checksum mismatch")
    # Joblib uses pickle; never use this loader on untrusted uploaded files.
    bundle = joblib.load(artifact_path)
    features = tuple(bundle["feature_contract"]["feature_columns"])
    try:
        validate_feature_contract(bundle["feature_contract"])
        feature_set = feature_set_for_columns(features)
    except ValueError:
        raise ValueError("persisted model feature contract mismatch") from None
    if (bundle["feature_contract"].get("source_incident_manifest_sha256") != manifest["source_incident_manifest_sha256"]
            or bundle["feature_contract"].get("feature_set", "frontier-baseline/v1") != feature_set):
        raise ValueError("persisted model feature contract mismatch")
    sampler = None
    if feature_set != "frontier-baseline/v1":
        reference = bundle["feature_contract"].get("vegetation")
        if not reference or reference != manifest.get("vegetation"):
            raise ValueError("vegetation bundle/run manifest mismatch")
        sampler = VegetationFeatureSampler(reference["manifest_path"], expected_sha256=reference["manifest_sha256"])
    contract = bundle["transition_contract"]
    if contract["transition_version"] != INCIDENT_TRANSITION_VERSION:
        raise ValueError("unsupported incident transition version")
    return IncidentTransitionModel(bundle["model"], feature_columns=features, vegetation_sampler=sampler,
        observation_calibration=SyntheticObservationCalibration(**bundle["observation_calibration"]),
        ignition_threshold=contract["ignition_threshold"], active_duration_steps=contract["active_duration_steps"],
        intensity_retention=contract["intensity_retention"], new_ignition_age_hours=contract["new_ignition_age_hours"],
        max_new_cells_per_step=contract["max_new_cells_per_step"], max_candidates=contract["max_candidates"],
        growth_fraction=contract["growth_fraction"])
