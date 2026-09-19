"""Consistent, atomic JSON serialization for fitted-model reports."""

import json
import os
from pathlib import Path
import tempfile
from typing import Any

from wildfire_data.core.hashing import sha256_file

PUBLIC_MODEL_KIND = 'completed-public-csv-models/v1'


def public_artifact(manifest_path, name):
    """Resolve a trusted run's relative artifact and verify its actual bytes."""
    path = Path(manifest_path).resolve()
    manifest = json.loads(path.read_text())
    if manifest.get('kind') != PUBLIC_MODEL_KIND or manifest.get('status') != 'complete':
        raise ValueError('A completed public CSV model run is required')
    artifact = manifest['artifacts'][name]
    relative = Path(artifact['path'])
    target = path.parent / relative
    if relative.is_absolute() or '..' in relative.parts or target.is_symlink() or not target.resolve().is_relative_to(path.parent):
        raise ValueError('Artifact path escapes its model run')
    if sha256_file(target) != artifact['sha256']:
        raise ValueError('Model artifact checksum mismatch')
    return target, manifest


def write_model_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            json.dump(document, destination, indent=2, sort_keys=True, allow_nan=False)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
