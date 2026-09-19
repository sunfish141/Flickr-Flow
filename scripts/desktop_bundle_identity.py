"""Bind verification evidence to every file in a specific desktop distribution."""
import hashlib
from pathlib import Path


def bundle_digest(directory):
    root = Path(directory)
    identity = hashlib.sha256()
    for file in sorted(root.rglob('*')):
        if file.is_symlink():
            raise ValueError('Unexpected symlink in desktop distribution')
        if file.is_file():
            identity.update(file.relative_to(root).as_posix().encode('utf-8') + b'\0')
            with file.open('rb') as stream:
                identity.update(hashlib.file_digest(stream, 'sha256').digest())
    return identity.hexdigest()
