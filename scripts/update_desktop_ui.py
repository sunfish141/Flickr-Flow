"""Refresh compiled UI assets in the local build without replacing a running EXE.

Only generated frontend files are replaced; retain a backup and require fresh
frozen acceptance before the release packager accepts the new distribution.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
FILES = ('app.js', 'app.css', 'app.js.LEGAL.txt', 'THIRD_PARTY_LICENSES.txt')


def main():
    source = ROOT / 'src/wildfire_data/web/static'
    build = ROOT / 'dist/WildfireAtlas'
    destination = build / '_internal/wildfire_data/web/static'
    info = build / 'BUILD-INFO.json'
    if not info.is_file() or not destination.is_dir():
        raise SystemExit('Build the desktop runtime first')
    metadata = json.loads(info.read_text(encoding='utf-8'))
    for name in FILES:
        if not (source / name).is_file() or not (destination / name).is_file():
            raise SystemExit(f'Missing compiled UI asset: {name}')
    backup = ROOT / 'build/desktop-ui-backups' / datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(info, backup / info.name)
    for name in FILES:
        shutil.copy2(destination / name, backup / name)
        shutil.copy2(source / name, destination / name)
    metadata['status'] = 'UI updated; fresh frozen acceptance required'
    metadata['ui_sha256'] = {name: hashlib.sha256((destination / name).read_bytes()).hexdigest() for name in FILES}
    metadata['installed_bytes'] = sum(p.stat().st_size for p in build.rglob('*') if p.is_file())
    info.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print(json.dumps({'updated': list(FILES), 'backup': str(backup)}, indent=2))


if __name__ == '__main__':
    main()
