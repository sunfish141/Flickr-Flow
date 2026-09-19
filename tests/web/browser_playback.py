"""Browser race/replay checks using explicit API fixtures, not live observations."""
import asyncio
import copy
from datetime import datetime, timedelta

from playwright.async_api import expect


class HeldResponse:
    """Let a canceled response finish after another UI action has occurred."""
    def __init__(self, frame):
        self.frame = frame
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()

    async def __call__(self, route):
        self.started.set()
        await self.release.wait()
        try:
            await route.fulfill(json=self.frame)
        finally:
            self.finished.set()

    async def wait(self):
        await asyncio.wait_for(self.started.wait(), 10)

    async def finish(self):
        self.release.set()
        await asyncio.wait_for(self.finished.wait(), 10)


async def verify_playback(page, base_url, checks):
    response = await page.request.post(base_url + '/api/seed', data={'ignitions': [
        {'latitude': 53.02, 'longitude': -117.31, 'intensity': .8}]})
    assert response.ok
    seed = await response.json()

    def frame(step):
        result = copy.deepcopy(seed)
        result['state']['step_index'] = step
        result['elapsed_hours'] = step * 12
        origin = datetime.fromisoformat(result['origin_at'].replace('Z', '+00:00'))
        result['valid_at'] = (origin + timedelta(hours=step * 12)).isoformat()
        return result

    await page.locator('#close-inspector').click()
    await page.locator('#place-tab').click()
    await page.locator('#reset').click()
    await page.locator('#coordinate-place').click()
    await expect(page.locator('#active-count')).to_have_text('1')
    old = HeldResponse(frame(1))
    replacement = HeldResponse(seed)
    await page.route('**/api/step', old)
    await page.route('**/api/seed', replacement)
    await page.locator('#step').click()
    await old.wait()
    await page.locator('#reset').click()
    await page.locator('#coordinate-place').click()
    await replacement.wait()
    await old.finish()
    await expect(page.locator('#playback-state')).to_have_text('LOADING')
    await expect(page.locator('#active-count')).to_have_text('0')
    await replacement.finish()
    await expect(page.locator('#active-count')).to_have_text('1')
    await expect(page.locator('#elapsed')).to_have_text('+0 hours')
    await page.unroute('**/api/step', old)
    await page.unroute('**/api/seed', replacement)
    checks.append('Reset discards late inference; old cleanup cannot clear a replacement seed request')

    # Hiding the tab cancels explicit initialization as well as playback.
    await page.locator('#reset').click()
    hidden = HeldResponse(seed)
    await page.route('**/api/seed', hidden)
    await page.locator('#coordinate-place').click()
    await hidden.wait()
    await page.evaluate("""() => {
      Object.defineProperty(document, 'hidden', { configurable: true, value: true });
      document.dispatchEvent(new Event('visibilitychange'));
      delete document.hidden;
    }""")
    await expect(page.locator('#status')).to_contain_text('Request canceled')
    await hidden.finish()
    await expect(page.locator('#active-count')).to_have_text('0')
    await expect(page.locator('#playback-state')).to_have_text('PAUSED')
    await page.unroute('**/api/seed', hidden)
    await page.locator('#coordinate-place').click()
    await expect(page.locator('#active-count')).to_have_text('1')

    async def failed(route):
        await route.fulfill(status=503, json={'detail': 'Fixture provider unavailable'})
    await page.route('**/api/step', failed)
    await page.locator('#step').click()
    await expect(page.locator('#status')).to_contain_text('Fixture provider unavailable')
    await expect(page.locator('#elapsed')).to_have_text('+0 hours')
    await expect(page.locator('#active-count')).to_have_text('1')
    await expect(page.locator('#step')).to_be_enabled()
    await page.unroute('**/api/step', failed)

    stale = HeldResponse({**seed, 'active_count': 99, 'metadata': {
        'eligible_detection_count': 99, 'recent_detections_excluded': 0, 'as_of': seed['origin_at']}})
    await page.route('**/api/firms', stale)
    await page.locator('#firms-tab').click()
    await page.locator('#load-firms').click()
    await stale.wait()
    await page.locator('#place-tab').click()
    await stale.finish()
    await expect(page.locator('#active-count')).to_have_text('1')
    await page.unroute('**/api/firms', stale)

    async def cooldown(route):
        await route.fulfill(status=429, headers={'Retry-After': '2'}, json={'detail': 'Fixture retry later'})
    await page.route('**/api/firms', cooldown)
    await page.locator('#firms-tab').click()
    await page.locator('#load-firms').click()
    await expect(page.locator('#load-firms')).to_contain_text('Retry in')
    await expect(page.locator('#load-firms')).to_be_disabled()
    await expect(page.locator('#load-firms')).to_be_enabled(timeout=5000)
    await page.unroute('**/api/firms', cooldown)
    checks.append('Tab hiding and source changes cancel pending loads; provider errors preserve frames; retry cooldown recovers')

    bounds = dict(west=-179, south=24, east=-52, north=84)
    def historical(step):
        result = frame(step)
        result['finished'] = step == 4
        result['historical'] = {'start_date': '2026-05-11', 'date': f'2026-05-{11 + step // 2}',
            'bounds': bounds, 'detection_count': 3, 'cell_count': 1, 'points': [{
                'cell_id': 'historical-fixture', 'status': 'historical', 'latitude': 53.02,
                'longitude': -117.31, 'detection_count': 3}]}
        return result

    async def load_history(route):
        assert route.request.post_data_json == {'date': '2026-05-11', 'bounds': bounds}
        await route.fulfill(json=historical(0))
    requests = []
    async def next_day(route):
        body = route.request.post_data_json
        requests.append(body)
        assert body['historical'] == {'start_date': '2026-05-11', 'bounds': bounds}
        assert body['state'] == historical(body['state']['step_index'])['state']
        await route.fulfill(json=historical(body['state']['step_index'] + 2))
    await page.route('**/api/firms/historical', load_history)
    await page.route('**/api/step', next_day)
    await page.locator('#historical-mode').check()
    await page.locator('#historical-date').fill('2026-05-11')
    await page.locator('#load-firms').click()
    await expect(page.locator('.historical-summary')).to_contain_text('2026-05-11')
    await expect(page.locator('#timeline')).to_have_attribute('step', '2')
    await page.locator('#cell-search').fill('historical-fixture')
    await page.locator('#cell-list li button').click()
    await expect(page.locator('#point-title')).to_have_text('Historical FIRMS')
    await expect(page.locator('#point-details')).to_contain_text('2026-05-11')
    await page.locator('#close-inspector').click()
    await page.locator('#cell-search').fill('')
    await page.locator('#show-historical').uncheck()
    await expect(page.locator('#show-historical')).not_to_be_checked()
    # The accessible list retains observations even when their map layer is hidden.
    await expect(page.locator('#cell-list li')).to_have_count(2)
    await page.locator('#show-historical').check()
    for hours in (24, 48):
        await page.locator('#step').click()
        await expect(page.locator('#elapsed')).to_have_text(f'+{hours} hours')
    await expect(page.locator('#step')).to_be_disabled()
    await expect(page.locator('#play')).to_be_disabled()
    await page.locator('#timeline').fill('0')
    await page.locator('#play').click()
    await expect(page.locator('#elapsed')).to_have_text('+48 hours', timeout=10000)
    await expect(page.locator('#playback-state')).to_have_text('PAUSED')
    assert len(requests) == 2, 'Replay must use saved frames, without additional prediction requests'
    await page.unroute('**/api/firms/historical', load_history)
    await page.unroute('**/api/step', next_day)
    checks.append('Historical fixtures: fixed bounds, 24-hour steps, observation inspector/layer, saved-frame playback and stop at final day')
