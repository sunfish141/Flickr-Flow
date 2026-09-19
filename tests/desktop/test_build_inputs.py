import hashlib
import importlib.util
import io
from pathlib import Path
import zipfile

import pytest

spec = importlib.util.spec_from_file_location('desktop_model_input', Path(__file__).resolve().parents[2] / 'scripts/obtain_desktop_model_input.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def payload(names):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name in names: archive.writestr(name, '{}')
    return stream.getvalue()


@pytest.mark.parametrize('names', [['../frontier.joblib'], ['.env'], ['run_manifest.json', 'run_manifest.json'], ['nested/terrain.csv']])
def test_build_input_rejects_unlisted_or_unsafe_files_before_writing(tmp_path, names):
    data = payload(names)
    destination = tmp_path / 'output'
    with pytest.raises(ValueError):
        module.extract_verified(data, hashlib.sha256(data).hexdigest(), destination)
    assert not destination.exists()


def test_reviewed_archive_checksum_is_required(tmp_path):
    with pytest.raises(ValueError, match='checksum'):
        module.extract_verified(payload(module.NAMES), '0' * 64, tmp_path / 'output')


def test_acceptance_identity_covers_content_and_paths(tmp_path):
    spec = importlib.util.spec_from_file_location('desktop_bundle_identity',
        Path(__file__).resolve().parents[2] / 'scripts/desktop_bundle_identity.py')
    identity = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(identity)
    artifact = tmp_path / 'model.dat'
    artifact.write_bytes(b'first')
    original = identity.bundle_digest(tmp_path)
    assert original == identity.bundle_digest(tmp_path)
    artifact.write_bytes(b'second')
    assert original != identity.bundle_digest(tmp_path)
    artifact.write_bytes(b'first')
    artifact.rename(tmp_path / 'renamed.dat')
    assert original != identity.bundle_digest(tmp_path)
