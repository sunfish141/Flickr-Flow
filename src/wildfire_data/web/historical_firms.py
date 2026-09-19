"""Read compact daily FIRMS comparisons from the retained, verified archive."""

from collections import OrderedDict
from datetime import date, datetime, time, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re

from wildfire_data.core.grid import cell_from_wgs84, cell_from_id
from wildfire_data.web.live_firms import PRODUCTS

FIRST_DAY = date(2026, 5, 11)
LAST_DAY = date(2026, 8, 21)
MAX_SOURCE_BYTES = 64 * 1024 * 1024


class HistoricalFirmsError(ValueError):
    pass


def day_cutoff(day):
    """End of the selected UTC observation day, exclusive."""
    return datetime.combine(day + timedelta(days=1), time(), tzinfo=timezone.utc)


class HistoricalFirmsStore:
    """Snapshot coverage at startup; cache only four decoded observation days.

    Call under the app's historical-data lock. Coverage ledger entries select
    exact normalized hashes, including explicit empty feeds; missing files or
    incomplete products are errors, never inferred zero-fire days.
    """

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.coverage = {}
        self.cache = OrderedDict()
        for path in (self.root / 'manifests/coverage').rglob('*.json'):
            entry = json.loads(path.read_text())
            scope = entry.get('scope', {})
            if (scope.get('source') != 'NASA FIRMS' or scope.get('product') not in PRODUCTS
                    or scope.get('region') != 'United States and Canada' or scope.get('tile') is not None):
                continue
            day = scope.get('coverage_start', '')
            if day != scope.get('coverage_end') or not FIRST_DAY.isoformat() <= day <= LAST_DAY.isoformat():
                continue
            key = (day, scope['product'])
            if key not in self.coverage or entry['append_order'] > self.coverage[key]['append_order']:
                self.coverage[key] = entry

    @property
    def available(self):
        return bool(self.coverage)

    def _rows(self, day):
        key = day.isoformat()
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        rows = []
        for product in PRODUCTS:
            entry = self.coverage.get((key, product))
            if not entry or entry['status'] not in ('complete', 'empty-confirmed'):
                raise HistoricalFirmsError(f'Historical FIRMS coverage is incomplete for {key}. Restore all three retained VIIRS feeds.')
            if entry['status'] == 'empty-confirmed':
                continue
            hashes = entry.get('detail', {}).get('normalized_artifact_ids', [])
            if not hashes:
                raise HistoricalFirmsError(f'Historical FIRMS data is missing for {key}.')
            for identity in sorted(set(hashes)):
                if not re.fullmatch('[0-9a-f]{64}', identity):
                    raise HistoricalFirmsError('Invalid historical FIRMS artifact reference.')
                path = (self.root / f'normalized/fire-detections/acq-date={key}/{identity}.jsonl.gz').resolve()
                if not path.is_relative_to(self.root):
                    raise HistoricalFirmsError('Invalid historical FIRMS artifact location.')
                digest, size = hashlib.sha256(), 0
                try:
                    with gzip.open(path, 'rb') as stream:
                        while line := stream.readline(MAX_SOURCE_BYTES + 1):
                            size += len(line)
                            if size > MAX_SOURCE_BYTES:
                                raise HistoricalFirmsError('Historical FIRMS artifact exceeds the preview read budget.')
                            digest.update(line)
                            row = json.loads(line)
                            acquired = datetime.fromisoformat(row['acquired_at'].replace('Z', '+00:00'))
                            lat, lon, bright = (float(row[k]) for k in ('latitude', 'longitude', 'bright_ti4'))
                            if (row['record_type'] != 'firms_detection' or row['provenance']['product'] != product
                                    or acquired.tzinfo is None or acquired.astimezone(timezone.utc).date() != day
                                    or not all(math.isfinite(v) for v in (lat, lon, bright))
                                    or not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= bright <= 10000)):
                                raise HistoricalFirmsError('Invalid historical FIRMS record.')
                            rows.append((lat, lon, bright, acquired.isoformat(), product))
                    if digest.hexdigest() != identity:
                        raise HistoricalFirmsError('Historical FIRMS checksum verification failed.')
                except (OSError, EOFError, KeyError, TypeError, ValueError) as exc:
                    if isinstance(exc, HistoricalFirmsError):
                        raise
                    raise HistoricalFirmsError(f'Historical FIRMS data for {key} is missing or unreadable. Restore the retained archive.') from None
        self.cache[key] = tuple(rows)
        if len(self.cache) > 4:
            self.cache.popitem(last=False)
        return self.cache[key]

    def load(self, day, bounds):
        if not FIRST_DAY <= day <= LAST_DAY:
            raise HistoricalFirmsError('Historical FIRMS dates must be May 11–August 21, 2026.')
        west, south, east, north = bounds
        rows, seen, cells = [], set(), {}
        for lat, lon, bright, acquired, product in self._rows(day):
            if not west <= lon <= east or not south <= lat <= north:
                continue
            identity = (product, lat, lon, acquired)
            if identity in seen:
                continue
            seen.add(identity)
            rows.append({'latitude': lat, 'longitude': lon, 'bright_ti4': bright,
                         'acquired_at': acquired, 'provenance': {'product': product}})
            cell_id = cell_from_wgs84(latitude=lat, longitude=lon).cell_id
            cells[cell_id] = cells.get(cell_id, 0) + 1
        points = []
        for cell_id, count in sorted(cells.items()):
            lat, lon = cell_from_id(cell_id).center_wgs84
            points.append({'cell_id': cell_id, 'latitude': lat, 'longitude': lon,
                           'status': 'historical', 'detection_count': count})
        return rows, {'date': day.isoformat(), 'points': points, 'detection_count': len(rows),
                      'cell_count': len(points), 'bounds': dict(zip(('west', 'south', 'east', 'north'), bounds))}
