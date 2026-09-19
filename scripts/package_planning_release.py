"""Create local handoff ZIPs and checksums; never upload or publish a release."""
import hashlib
import json
from pathlib import Path
import platform
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def tree_digest(source):
    """Bind acceptance evidence to the exact packaged file tree, not its name."""
    digest = hashlib.sha256()
    for path in sorted(source.rglob('*')):
        if path.is_file():
            digest.update(path.relative_to(source).as_posix().encode('utf-8') + b'\0')
            with path.open('rb') as handle:
                digest.update(hashlib.file_digest(handle, 'sha256').digest())
    return digest.hexdigest()


def zip_tree(source, target, *, prefix='', extra_files=()):
    if target.exists():
        raise ValueError(f'Refusing to overwrite existing handoff archive: {target}')
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(source.rglob('*')):
            if path.is_file() and not path.is_symlink():
                archive.write(path, prefix + path.relative_to(source).as_posix())
        for path, name in extra_files:
            archive.write(path, name)
    with target.open('rb') as handle:
        checksum = hashlib.file_digest(handle, 'sha256').hexdigest()
    return {'file': target.name, 'sha256': checksum, 'bytes': target.stat().st_size}


def main():
    from wildfire_data.planning.packs import Packs
    from wildfire_data.planning.contracts import engine_identity
    destination = ROOT / 'releases' / 'offline-planning-v1'
    destination.mkdir(parents=True, exist_ok=True)
    packs = ROOT / 'data' / 'planning-packs-v1'
    registry = Packs(packs)
    if registry.errors or len(registry.entries) != 2:
        raise SystemExit('Both verified real packs are required')
    distribution = ROOT / 'dist' / 'WildfirePlanner'
    build = json.loads((distribution/'BUILD-INFO.json').read_text(encoding='utf-8'))
    if build['engine_version'] != engine_identity():
        raise SystemExit('Rebuild the application before making a handoff')
    handoff_docs = [(ROOT/'docs'/'offline-planning-quickstart.md', 'READ-ME-FIRST.md'),
                    (ROOT/'docs'/'offline-planning-acceptance.md', 'VERIFICATION.md'),
                    (ROOT/'docs'/'offline-planning-quickstart.md', 'offline-planning-quickstart.md'),
                    (ROOT/'docs'/'offline-planning-acceptance.md', 'offline-planning-acceptance.md'),
                    (ROOT/'docs'/'offline-planning-v1.md', 'offline-planning-v1.md'),
                    (ROOT/'artifacts'/'planning-smoke'/'frozen'/'resource-report.json', 'verification/resource-report.json'),
                    (ROOT/'artifacts'/'planning-smoke'/'frozen'/'browser-report.json', 'verification/browser-report.json'),
                    (ROOT/'artifacts'/'planning-smoke'/'frozen'/'import-report.json', 'verification/import-report.json')]
    if not all(path.is_file() for path, _ in handoff_docs):
        raise SystemExit('Quickstart and measured acceptance evidence are required for handoff')
    evidence = ROOT/'artifacts'/'planning-smoke'/'frozen'
    resources = json.loads((evidence/'resource-report.json').read_text(encoding='utf-8'))
    browser = json.loads((evidence/'browser-report.json').read_text(encoding='utf-8'))
    if not resources.get('frozen') or resources.get('installed_tree_sha256') != tree_digest(distribution):
        raise SystemExit('Run frozen smoke acceptance against this exact build before handoff')
    if browser['errors'] or browser['externalRequests'] or {r['pack'] for r in browser['runs']} != set(registry.entries):
        raise SystemExit('Both real-pack browser checks must pass before handoff')
    imports = json.loads((evidence/'import-report.json').read_text(encoding='utf-8'))
    if {case['pack'] for case in imports['cases']} != set(registry.entries):
        raise SystemExit('Both real-pack exports must pass import round-trip verification')
    for case in imports['cases']:
        payload = (evidence/f'{case["pack"]}.json').read_bytes()
        if hashlib.sha256(payload).hexdigest() != case['source_export_sha256']:
            raise SystemExit('Import verification is stale for these browser exports')
    info = [zip_tree(packs, destination/'planning-packs-v1.zip')]
    info.append(zip_tree(distribution, destination/f'WildfirePlanner-UNSIGNED-{platform.system()}-{platform.machine()}.zip',
                         prefix='WildfirePlanner/', extra_files=handoff_docs))
    (destination/'checksums.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    print(json.dumps(info, indent=2))


if __name__ == '__main__':
    main()
