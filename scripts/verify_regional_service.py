"""Real, offline full-region checks well outside the former pilot squares."""
import time


LOCATIONS = [
    ('Northern Alberta', 'alberta', 58.6, -116.1),
    ('Southeastern Alberta', 'alberta', 49.7, -110.3),
    ('Southwestern Colorado', 'colorado', 37.4, -107.8),
    ('Eastern Colorado', 'colorado', 38.3, -102.4),
]


def verify(client):
    config = client.get('/api/config').json()
    if not any(r.get('tiled') for r in config['local_spread']['regions']):
        return {'installed': False, 'locations': []}
    assert {r['id'] for r in config['local_spread']['regions']} == {'alberta', 'colorado'}
    desktop = client.get('/api/desktop').json()
    assert not desktop['online_enabled']
    reports = []
    for label, identity, lat, lon in LOCATIONS:
        started = time.monotonic()
        attempts = []
        # A coordinate can land on a road/unknown polygon. Try a documented
        # nearby offset, never change the requested engine or invent fuel.
        for dy, dx in [(0,0), (.002,0), (0,.002), (-.002,0), (0,-.002), (.004,.004)]:
            point = {'latitude': lat+dy, 'longitude': lon+dx, 'intensity': 1}
            response = client.post('/api/map/seed', json={'ignitions': [point]})
            attempts.append({'point': point, 'status': response.status_code})
            if response.status_code == 200:
                break
            assert response.status_code == 422 and 'supported vegetation' in response.text, response.text
        response.raise_for_status()
        seed = response.json()
        assert seed['local'] and seed['expanding'] and seed['region_id'] == identity
        assert seed['perimeters']['features']
        body = {'state': seed['state'], 'origin_at': seed['origin_at']}
        response = client.post('/api/landscape/step', json=body)
        response.raise_for_status()
        first = response.json()
        repeated = client.post('/api/landscape/step', json=body)
        repeated.raise_for_status()
        assert repeated.json() == first, 'Same recorded inputs must reproduce the same frame'
        response = client.post('/api/landscape/step', json={'state': first['state'], 'origin_at': first['origin_at']})
        response.raise_for_status()
        final = response.json()
        assert final['elapsed_hours'] == 24 and final['region_id'] == identity
        assert final['perimeters']['features']
        reports.append({'location': label, 'region': identity, 'attempts': attempts,
            'elapsed_hours': 24, 'tiles': len(final['state']['tiles']),
            'run_seconds_including_replay': time.monotonic()-started,
            'active_area_m2': final['active_area_m2'], 'burned_area_m2': final['burned_area_m2'],
            'reproduced': True})
    # Inside the regional envelope but outside Alberta's actual western border.
    outside = client.post('/api/map/seed', json={'ignitions': [{'latitude': 50., 'longitude': -119., 'intensity': 1}]})
    assert outside.status_code == 422 and 'installed province/state' in outside.text
    return {'installed': True, 'locations': reports, 'political_boundary_enforced': True}
