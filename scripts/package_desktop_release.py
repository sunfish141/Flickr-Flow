"""Create a new, checksummed desktop handoff only after frozen acceptance."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
from desktop_bundle_identity import bundle_digest

ROOT = Path(__file__).resolve().parents[1]


def main():
    evidence = ROOT / 'artifacts/desktop-smoke/frozen'
    report = json.loads((evidence / 'acceptance.json').read_text(encoding='utf-8'))
    if not report['frozen'] or not report['interruption_recovered'] or not report['native_window']['ready']:
        raise SystemExit('Complete frozen acceptance is required before packaging')
    source = ROOT / 'dist/WildfireAtlas'
    if report.get('bundle_sha256') != bundle_digest(source):
        raise SystemExit('Distribution differs from the verified build; rerun frozen acceptance')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    release = ROOT / 'releases' / f'desktop-preview-{stamp}'
    release.mkdir(parents=True, exist_ok=False)
    verification = ('# Wildfire Atlas internal desktop verification\n\n'
        'Unsigned Windows preview, research-only; no operational validation.\n\n'
        f'- Installed bytes: {report["installed_bytes"]:,}\n'
        f'- Service startup: {report["startup_seconds"]:.2f} seconds\n'
        f'- Full native-window check: {report["native_window_check_seconds"]:.2f} seconds '
        '(includes rendering, deliberate screenshot delay and shutdown)\n'
        f'- Default 24-hour run: {report["browser"]["planner_run_seconds"]:.2f} seconds\n'
        f'- Peak tested app/browser process-tree RSS: {report["peak_process_tree_rss_bytes"]:,} bytes\n'
        '- Passed automatic regional polygon routing, labelled online 1 km grid placement/playback, '
        'grid-cell polygon geometry, offline overview, missing-pack handling, automatic map '
        'reconnection, responsive layout and native rendering. Legacy classifier, storage/export and interrupted-worker '
        'recovery checks remain available behind development APIs.\n'
        '- Tests used a Windows development machine and blocked/intercepted outbound browser requests. '
        'An independent clean-install/network-disconnection test and a reference 8 GB device remain necessary.\n'
        '- Live NASA FIRMS was not fetched: no credential configured. Mac/Linux native packages are unverified.\n'
        '- Public signing/notarization, licensing review and domain validation remain release gates.\n')
    (release / 'VERIFICATION.md').write_text(verification, encoding='utf-8')
    shutil.copy2(ROOT / 'docs/desktop-quickstart.md', release / 'QUICKSTART.md')
    shutil.copy2(evidence / 'acceptance.json', release / 'acceptance.json')
    archive = release / 'WildfireAtlas-Windows-x64-UNSIGNED.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for file in sorted(source.rglob('*')):
            if file.is_file():
                if file.name == '.env': raise ValueError('Credential file must never be shipped')
                bundle.write(file, 'WildfireAtlas/' + file.relative_to(source).as_posix())
        bundle.write(release / 'VERIFICATION.md', 'VERIFICATION.md')
        bundle.write(release / 'QUICKSTART.md', 'QUICKSTART.md')
    with archive.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    (release / 'SHA256SUMS').write_text(f'{checksum}  {archive.name}\n', encoding='utf-8')
    print(json.dumps({'release': str(release), 'archive_bytes': archive.stat().st_size, 'sha256': checksum}, indent=2))


if __name__ == '__main__':
    main()
