"""Round-trip real packaged-browser exports through a fresh, isolated API store."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from uuid import uuid4

from fastapi.testclient import TestClient
from wildfire_data.planning.app import create_app

ROOT = Path(__file__).resolve().parents[1]


def main():
    evidence = ROOT/'artifacts'/'planning-smoke'/'frozen'
    results = []
    with tempfile.TemporaryDirectory(prefix='Planning import verification ') as data:
        with TestClient(create_app(data_root=Path(data), pack_root=ROOT/'data'/'planning-packs-v1'),
                        base_url='http://127.0.0.1') as client:
            for pack in ('hinton-alberta', 'black-hawk-colorado'):
                payload = (evidence/f'{pack}.json').read_bytes()
                original = json.loads(payload)
                response = client.post('/api/scenarios/import', json={
                    'request_id': str(uuid4()), 'document': original})
                assert response.status_code == 200, response.text
                imported = response.json()
                assert imported['imported'] and imported['continuation_blocked']
                base = f'/api/scenarios/{imported["id"]}'
                response = client.get(base+'/export?format=json')
                assert response.status_code == 200, response.text
                exported = response.json()
                for key in ('definition', 'pack_snapshot', 'frames', 'playback_hour'):
                    assert exported[key] == original[key], key
                # Pydantic serializes a UTC +00:00 offset as Z; the instant must
                # remain identical, not the interchangeable offset spelling.
                assert datetime.fromisoformat(exported['created_at']) == datetime.fromisoformat(original['created_at'])
                assert len(exported['frames']) == 25
                assert client.get(base+'/export?format=html').status_code == 200
                assert client.get(base+'/export?format=geojson').status_code == 200
                # An imported result cannot silently become a trusted continuation.
                assert client.post(base+'/run', json={
                    'request_id': str(uuid4()), 'expected_revision': imported['revision']}).status_code == 409
                results.append({'pack': pack, 'source_export_sha256': hashlib.sha256(payload).hexdigest(),
                                'frames_retained_exactly': 25, 'definition_and_snapshot_retained_exactly': True,
                                'imported_results_remain_unverified': True})
    report = {'completed_at': datetime.now(timezone.utc).isoformat(), 'cases': results,
              'scope': 'Frozen browser exports imported into a fresh source API using the same pinned runtime; not cross-platform evidence.'}
    (evidence/'import-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
