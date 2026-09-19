"""Collect offline notices from installed, locked runtime distributions."""
from importlib.metadata import distribution
from pathlib import Path
import sys


def notices(root):
    parts = ['WildfirePlanner — unsigned internal research build\n',
        'Application code: Apache-2.0 (see LICENSE).\n',
        'Prepared NALCMS cover: CC BY 4.0. CEC (2024), North American Environmental Atlas, NALCMS Ed. 2.0; '
        'Canada Centre for Remote Sensing / Natural Resources Canada; USGS; CONABIO; CONAFOR; INEGI. '
        'Reprojected by nearest neighbor to 100 m and clipped; not a new measurement.\n',
        'Prepared roads: Open Database License 1.0. © OpenStreetMap contributors, Overture Maps Foundation, TomTom '
        'and named per-feature sources. Release 2026-08-19.0. Reprojected, clipped and normalized. '
        'The prepared road database is included as planning_resources/*/roads.json under ODbL. '
        'These data licenses do not imply model validation.\n',
        'License references: https://creativecommons.org/licenses/by/4.0/ and https://opendatacommons.org/licenses/odbl/1-0/\n']
    for line in (root/'requirements-planning.lock').read_text().splitlines():
        if '==' not in line or line.startswith('#'):
            continue
        name = line.split('==')[0]
        try:
            package = distribution(name)
        except Exception:
            continue  # platform-conditional dependency not present
        parts.append(f'\n--- {package.metadata["Name"]} {package.version} ---\n')
        found = False
        for entry in package.files or ():
            if any(word in str(entry).lower() for word in ('license', 'notice', 'copying')) and str(entry).endswith(('.txt', '.md', '.rst', 'LICENSE', 'NOTICE', 'COPYING')):
                file = package.locate_file(entry)
                if file.is_file():
                    parts.append(file.read_text(encoding='utf-8', errors='replace')); found = True
        if not found:
            parts.append(f'License metadata: {package.metadata.get("License-Expression") or package.metadata.get("License", "See package distribution")}\n')
    python_license = Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.exists():
        parts.append('\n--- Python ---\n' + python_license.read_text(encoding='utf-8'))
    return '\n'.join(parts)
