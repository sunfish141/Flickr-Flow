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


@pytest.fixture
def regional_importer():
    spec = importlib.util.spec_from_file_location('regional_build_input',
        Path(__file__).resolve().parents[2] / 'scripts/obtain_regional_build_input.py')
    importer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(importer)
    return importer


@pytest.mark.parametrize('names', [['../index.json'], ['.env'], ['index.json', 'index.json'], ['alberta/../../index.json']])
def test_regional_archive_rejects_unexpected_paths(tmp_path, regional_importer, names):
    data = payload(names)
    archive = tmp_path / 'input.zip'
    archive.write_bytes(data)
    with pytest.raises(ValueError, match='paths'):
        regional_importer.unpack(archive, tmp_path / 'output', hashlib.sha256(data).hexdigest())
    assert not (tmp_path / 'output').exists()


def test_regional_archive_rejects_links_and_size_before_install(tmp_path, regional_importer, monkeypatch):
    archive = tmp_path / 'input.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        for name in sorted(regional_importer.ALLOWED):
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3
            entry.external_attr = (0o120777 if name == 'index.json' else 0o100600) << 16
            bundle.writestr(entry, 'outside')
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='links'):
        regional_importer.unpack(archive, tmp_path / 'output', checksum)
    monkeypatch.setattr(regional_importer, 'MAX_BYTES', 1)
    with pytest.raises(ValueError, match='cap'):
        regional_importer.unpack(archive, tmp_path / 'output', checksum)
    assert not (tmp_path / 'output').exists()


def test_regional_archive_requires_checksum_and_preserves_existing_target(tmp_path, regional_importer):
    data = payload(regional_importer.ALLOWED)
    archive = tmp_path / 'input.zip'
    archive.write_bytes(data)
    target = tmp_path / 'existing'
    target.mkdir()
    retained = target / 'user-file'
    retained.write_text('retain')
    with pytest.raises(ValueError, match='checksum'):
        regional_importer.unpack(archive, target, '0'*64)
    with pytest.raises(ValueError, match='retained'):
        regional_importer.unpack(archive, target, hashlib.sha256(data).hexdigest())
    assert retained.read_text() == 'retain'
