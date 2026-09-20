import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient

from wildfire_data.model.incident_transition import IncidentTransitionModel
from wildfire_data.model.recursive_transition import RECURSIVE_MODEL_FEATURE_COLUMNS
from wildfire_data.web.app import create_app
from wildfire_data.web.security import MAX_REQUEST_BYTES, RequestBoundary
from web.test_web_app import SpreadEstimator, terrain
from model.test_spread import FuelPatch


class SecurityTests(unittest.TestCase):
    def setUp(self):
        model = IncidentTransitionModel(SpreadEstimator(), feature_columns=RECURSIVE_MODEL_FEATURE_COLUMNS,
                                        ignition_threshold=.2)
        self.app = create_app(model=model, terrain_provider=terrain, landscape=FuelPatch(), vegetation_sampler=object())
        self.client = TestClient(self.app, base_url='http://localhost')
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def seed(self):
        return self.client.post('/api/seed', json={'ignitions': [
            {'latitude': 53.02, 'longitude': -117.31, 'intensity': .8}]}).json()

    def test_security_headers_and_asset_boundary(self):
        for path in ['/', '/static/app.js', '/api/config', '/missing']:
            response = self.client.get(path)
            self.assertIn("frame-ancestors 'none'", response.headers['content-security-policy'])
            self.assertIn("script-src 'self'", response.headers['content-security-policy'])
            self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
            self.assertEqual(response.headers['referrer-policy'], 'strict-origin-when-cross-origin')
        for path in ['/config/.env', '/static/%2e%2e/app.py', '/docs', '/openapi.json']:
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_browser_origin_and_host_checks(self):
        for headers in [{'origin': 'https://evil.example'}, {'origin': 'null'},
                        {'sec-fetch-site': 'cross-site'}, {'origin': 'http://localhost:81'}]:
            self.assertEqual(self.client.post('/api/firms', headers=headers).status_code, 403)
        self.assertEqual(self.client.get('/api/config', headers={'origin': 'http://localhost'}).status_code, 200)
        self.assertEqual(self.client.get('/', headers={'host': 'evil.example'}).status_code, 400)

    def test_oversized_content_length_and_actual_body(self):
        self.assertEqual(self.client.post('/api/step', headers={'content-length': str(MAX_REQUEST_BYTES + 1)}).status_code, 413)
        self.assertEqual(self.client.post('/api/step', content=b' ' * (MAX_REQUEST_BYTES + 1),
            headers={'content-length': '1', 'content-type': 'application/json'}).status_code, 413)
        self.assertEqual(self.client.post('/api/step', headers={'content-length': '-1'}).status_code, 400)

    def test_json_required_and_validation_does_not_echo_inputs(self):
        self.assertEqual(self.client.post('/api/seed', content='{}', headers={'content-type': 'text/plain'}).status_code, 415)
        response = self.client.post('/api/seed', json={'private-value': 'do-not-echo'})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('do-not-echo', response.text)
        self.assertNotIn('input', response.json()['detail'][0])

    def test_state_size_calendar_and_projected_coordinates_are_bounded(self):
        frame = self.seed()
        body = {'state': frame['state'], 'origin_at': frame['origin_at']}
        body['state']['active_cells'] *= 10001
        self.assertEqual(self.client.post('/api/step', json=body).status_code, 422)
        body['state']['active_cells'] = []
        body['state']['burned_cell_ids'] = ['naea-1km:x=0:y=0'] * 100001
        self.assertEqual(self.client.post('/api/step', json=body).status_code, 422)
        for cell_id in ['naea-1km:x=9999999999999999:y=0', 'naea-1km:x=00:y=0']:
            body['state']['burned_cell_ids'] = [cell_id]
            self.assertEqual(self.client.post('/api/step', json=body).status_code, 422)
        body['state']['burned_cell_ids'] = []
        body['origin_at'] = '9999-12-31T23:59:59Z'
        self.assertEqual(self.client.post('/api/step', json=body).status_code, 422)

    def test_busy_inference_fails_fast_and_recovers(self):
        # Seeding also samples terrain. Install the latch afterward so only
        # the actual in-flight step can signal that it holds the inference lock.
        frame = self.seed()
        started, release = Event(), Event()
        def slow_terrain(cell):
            started.set()
            release.wait(10)
            return terrain(cell)
        self.app.state.runtime.terrain = slow_terrain
        body = {'state': frame['state'], 'origin_at': frame['origin_at']}
        with ThreadPoolExecutor() as pool:
            first = pool.submit(self.client.post, '/api/step', json=body)
            try:
                self.assertTrue(started.wait(5))
                second = self.client.post('/api/step', json=body)
                self.assertEqual(second.status_code, 503)
                self.assertEqual(second.headers['retry-after'], '3')
            finally:
                release.set()
            self.assertEqual(first.result().status_code, 200)
        self.assertEqual(self.client.post('/api/step', json=body).status_code, 200)


class StreamingBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunked_body_stops_before_downstream_and_releases_capacity(self):
        called, output = [], []
        async def downstream(scope, receive, send):
            called.append(True)
        boundary = RequestBoundary(downstream)
        chunks = iter([{'type': 'http.request', 'body': b'x' * (MAX_REQUEST_BYTES // 2 + 1), 'more_body': True}] * 2)
        async def receive():
            return next(chunks)
        async def send(message):
            output.append(message)
        await boundary({'type': 'http', 'method': 'POST', 'path': '/api/step', 'scheme': 'http', 'headers': []}, receive, send)
        self.assertFalse(called)
        self.assertEqual(output[0]['status'], 413)
        self.assertEqual(boundary.inflight, 0)

    async def test_capacity_rejection_does_not_read_request(self):
        output = []
        async def unexpected(*args):
            self.fail('Rejected work must not enter downstream or read a body')
        async def send(message):
            output.append(message)
        boundary = RequestBoundary(unexpected)
        boundary.inflight = 8
        await boundary({'type': 'http', 'method': 'POST', 'path': '/api/step', 'scheme': 'http', 'headers': []}, unexpected, send)
        self.assertEqual(output[0]['status'], 503)


if __name__ == '__main__':
    unittest.main()
