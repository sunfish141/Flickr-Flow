"""Stable hashes for artifact verification and deterministic sampling."""

import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def stable_fraction(value: str) -> float:
    return int(hashlib.sha256(value.encode()).hexdigest()[:13], 16) / 16**13
