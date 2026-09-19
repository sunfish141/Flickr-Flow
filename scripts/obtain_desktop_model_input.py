"""CI-only admission of an explicitly reviewed, checksummed inference bundle."""
import hashlib
import io
import os
from pathlib import Path
import re
import urllib.request
import zipfile

NAMES = {'run_manifest.json', 'frontier.joblib', 'terrain.csv', 'protocol.json', 'evaluation.json', 'weather.joblib'}


def extract_verified(payload, expected, target):
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError('Reviewed model archive checksum mismatch')
    target = Path(target)
    if target.exists():
        raise ValueError('Refusing to overwrite an existing model run')
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)) or set(names) != NAMES or sum(e.file_size for e in entries) > 100_000_000:
            raise ValueError('Model input must contain exactly the six allowlisted inference/provenance files, under 100 MB')
        if any(e.is_dir() or e.orig_filename != e.filename or (e.external_attr >> 16) & 0o170000 == 0o120000 for e in entries):
            raise ValueError('Unsupported model archive entry')
        target.mkdir(parents=True)
        for entry in entries:
            (target / entry.filename).write_bytes(archive.read(entry))
    # Admission checks bytes and JSON contracts only; never unpickle the download.
    from wildfire_data.core.model_artifacts import public_artifact
    for name in NAMES - {'run_manifest.json'}:
        public_artifact(target / 'run_manifest.json', name)


def main():
    url = os.environ.get('DESKTOP_MODEL_URL', '')
    checksum = os.environ.get('DESKTOP_MODEL_SHA256', '')
    if not url.startswith('https://') or not re.fullmatch('[a-f0-9]{64}', checksum):
        raise SystemExit('Configure reviewed DESKTOP_MODEL_URL and DESKTOP_MODEL_SHA256 repository variables')
    with urllib.request.urlopen(url, timeout=60) as response:
        if not response.url.startswith('https://'):
            raise ValueError('Model archive redirected away from HTTPS')
        payload = response.read(100_000_001)
    if len(payload) > 100_000_000:
        raise ValueError('Build input exceeds 100 MB')
    extract_verified(payload, checksum, Path('artifacts/public-csv'))


if __name__ == '__main__':
    main()
