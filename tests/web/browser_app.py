"""Exercise the production React bundle through public UI/API boundaries.

Requires a running app, Playwright Chromium, and `npm ci` in frontend/.
External tiles and live/historical FIRMS are stubbed; initial simulation steps
use the real API. Fixture availability is enabled only in this browser context.
"""
import asyncio
import base64
import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

from playwright.async_api import async_playwright, expect
from browser_playback import verify_playback


async def main(base_url):
    output = Path('artifacts/web-react-preview')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'verification.json').unlink(missing_ok=True)
    axe_source = Path('frontend/node_modules/axe-core/axe.min.js').read_text()
    checks, audits = [], []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
        # Stub before navigation: the basemap loads automatically on startup.
        pixel = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        await page.route('https://tile.openstreetmap.org/**', lambda route: route.fulfill(body=pixel, content_type='image/png'))
        errors, external = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: external.append(request.url) if not request.url.startswith(base_url) else None)
        await page.add_init_script("window.cspViolations = []; document.addEventListener('securitypolicyviolation', e => window.cspViolations.push(e.violatedDirective))")
        async def configured(route):
            response = await route.fetch()
            data = await response.json()
            data['firms_configured'] = True
            data['historical_firms'] = {'available': True, 'min_date': '2026-05-11', 'max_date': '2026-08-21'}
            await route.fulfill(response=response, json=data)
        await page.route('**/api/config', configured)
        await page.route('**/static/axe-test.js', lambda route: route.fulfill(body=axe_source, content_type='text/javascript'))
        async def audit(label):
            if not await page.evaluate('typeof axe !== "undefined"'):
                await page.add_script_tag(url=base_url + '/static/axe-test.js')
            result = await page.evaluate("async () => { const r = await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}}); return {violations:r.violations, incomplete:r.incomplete}; }")
            audits.append({'label': label, **result})
            (output / 'accessibility.json').write_text(json.dumps(audits, indent=2))
            assert not result['violations'], json.dumps(result['violations'], indent=2)
        await page.goto(base_url)
        await expect(page.locator('#place')).to_be_enabled(timeout=60000)
        await audit('desktop initial')
        await expect(page.locator('#show-basemap')).to_be_checked()
        await page.wait_for_function("Array.from(document.querySelectorAll('.leaflet-tile-loaded')).some(tile => tile.complete && tile.naturalWidth > 0)")
        await expect(page.locator('.leaflet-control-attribution')).to_contain_text('OpenStreetMap')
        assert external and all(url.startswith('https://tile.openstreetmap.org/') for url in external), external
        checks.append('Basemap tiles load automatically with attribution; no other third-party requests')
        await page.keyboard.press('Tab')
        await expect(page.get_by_role('link', name='Skip to scenario controls')).to_be_focused()
        await page.keyboard.press('Enter')
        await expect(page.locator('#scenario-controls')).to_be_focused()
        await page.locator('.coordinates summary').focus()
        await page.keyboard.press('Space')
        await expect(page.locator('#latitude')).to_be_visible()
        await page.locator('#intensity').fill('80')
        await page.locator('#coordinate-place').focus()
        await page.keyboard.press('Enter')
        await expect(page.locator('#active-count')).to_have_text('1')
        await page.locator('#cell-list summary').click()
        first_cell = page.locator('#cell-list li button').first
        await first_cell.focus()
        await page.keyboard.press('Enter')
        await expect(page.locator('#point-title')).to_be_focused()
        await expect(page.locator('#point-details')).to_contain_text('80%')
        await expect(page.locator('#vegetation-status')).not_to_have_text('Loading vegetation…', timeout=60000)
        await audit('desktop inspector')
        await page.locator('#close-inspector').click()
        await expect(first_cell).to_be_focused()
        await page.locator('#step').click()
        await expect(page.locator('#elapsed')).to_have_text('+12 hours', timeout=60000)
        await page.locator('#timeline').fill('0')
        await expect(page.locator('#elapsed')).to_have_text('+0 hours')
        checks.append('Keyboard seed, inspector focus return, real inference, timeline')
        await page.locator('#reset').click()
        await page.locator('#coordinate-place').click()
        await expect(page.locator('#active-count')).to_have_text('1')
        # Freeze while a network response is pending, then resume real inference.
        started = asyncio.Event()
        async def delayed(route):
            started.set()
            await asyncio.sleep(1.2)
            try:
                await route.continue_()
            except Exception:
                pass
        await page.route('**/api/step', delayed)
        await page.locator('#speed').select_option('1')
        await page.locator('#play').click()
        await asyncio.wait_for(started.wait(), 10)
        await page.locator('#play').click()
        await page.wait_for_timeout(1600)
        await expect(page.locator('#elapsed')).to_have_text('+0 hours')
        await expect(page.locator('#playback-state')).to_have_text('PAUSED')
        await page.unroute('**/api/step', delayed)
        await page.locator('#play').click()
        await expect(page.locator('#elapsed')).to_have_text('+12 hours', timeout=60000)
        await page.locator('#play').click()
        for step in range(2, 13):
            await page.locator('#step').click()
            await expect(page.locator('#elapsed')).to_have_text(f'+{step * 12} hours', timeout=60000)
        checks.append('Pause in flight, resume, real inference to 144 hours')
        # Large display fixtures enter through the same FIRMS boundary as real data.
        response = await page.request.post(base_url + '/api/seed', data={'ignitions': [
            {'latitude': 53.02, 'longitude': -117.31, 'intensity': .8}]})
        fixture = await response.json()
        fixture['metadata'] = {'eligible_detection_count': 2000, 'recent_detections_excluded': 1, 'as_of': fixture['origin_at']}
        point = fixture['points'][0]
        fixture['points'] = [{**point, 'cell_id': f'display-fixture-{i}', 'latitude': 53 + (i % 50) * .01,
                              'longitude': -117 + (i // 50) * .01} for i in range(2000)]
        fixture['active_count'] = 2000
        fixture['points'][0]['source'] = '<img src=x onerror="window.injected=true">'
        requested = []
        async def firms(route):
            requested.append(route.request.post_data_json)
            await route.fulfill(json=fixture)
        await page.route('**/api/firms', firms)
        await page.locator('#firms-tab').click()
        await page.locator('#load-firms').click()
        await expect(page.locator('#status')).to_contain_text('Loaded 2000 observations')
        assert requested == [dict(west=-179, south=24, east=-52, north=84)]
        await expect(page.locator('#cell-list li')).to_have_count(25)
        await page.get_by_role('button', name='Next cells').click()
        await expect(page.locator('#cell-list li').first).to_contain_text('display-fixture-25')
        await page.locator('#cell-search').fill('display-fixture-1999')
        await expect(page.locator('#cell-list li')).to_have_count(1)
        await page.locator('#cell-search').fill('')
        # Dynamic strings are escaped by React, including API inspector values.
        await page.locator('#cell-list li button').first.click()
        await expect(page.locator('#point-details')).to_contain_text('<img src=x')
        assert not await page.evaluate('Boolean(window.injected)')
        await page.locator('#close-inspector').click()
        # Deterministic API fixture makes the 128-frame rolling window inexpensive.
        async def step_fixture(route):
            body = route.request.post_data_json
            index = body['state']['step_index'] + 1
            result = copy.deepcopy(fixture)
            result['state']['step_index'] = index
            result['elapsed_hours'] = index * 12
            origin = datetime.fromisoformat(result['origin_at'].replace('Z', '+00:00'))
            result['valid_at'] = (origin + timedelta(hours=index * 12)).isoformat()
            result['points'] = []
            await route.fulfill(json=result)
        await page.route('**/api/step', step_fixture)
        for index in range(1, 131):
            await page.locator('#step').click()
            await expect(page.locator('#elapsed')).to_have_text(f'+{index * 12} hours')
        await expect(page.locator('#timeline')).to_have_attribute('min', '3')
        await expect(page.locator('#timeline')).to_have_attribute('max', '130')
        await page.locator('#timeline').fill('30')
        await expect(page.locator('#elapsed')).to_have_text('+360 hours')
        checks.append('Full-region FIRMS, 2000-cell pagination, escaped API text, rolling 128-frame history')
        await page.locator('#help').click()
        await audit('help dialog')
        await page.keyboard.press('Escape')
        await expect(page.locator('#help')).to_be_focused()
        checks.append('Named native dialog, Escape and focus return')
        await page.locator('#show-basemap').uncheck()
        await expect(page.locator('.leaflet-tile')).to_have_count(0)
        await page.locator('#show-basemap').check()
        await expect(page.locator('.leaflet-tile-loaded').first).to_be_visible()
        await expect(page.locator('.leaflet-control-attribution')).to_contain_text('OpenStreetMap')
        await page.locator('#show-basemap').uncheck()
        await page.locator('#firms-tab').click()
        await page.locator('#load-firms').click()
        await expect(page.locator('#active-count')).to_have_text('2000')
        await page.locator('#cell-list li button').first.click()
        for width in (390, 320):
            await page.set_viewport_size({'width': width, 'height': 844})
            await page.locator('#inspector').scroll_into_view_if_needed()
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            await expect(page.locator('#show-active')).to_be_visible()
            await expect(page.locator('#active-count')).to_be_visible()
            await audit(f'mobile {width}px with inspector')
        await page.screenshot(path=str(output / 'mobile.png'), full_page=True)
        await page.set_viewport_size({'width': 1440, 'height': 1000})
        await page.locator('.sidebar').evaluate('(node) => node.scrollTop = 0')
        await page.screenshot(path=str(output / 'desktop.png'), full_page=True)
        await page.unroute('**/api/step', step_fixture)
        await page.unroute('**/api/firms', firms)
        await verify_playback(page, base_url, checks)
        assert not await page.evaluate('window.cspViolations'), await page.evaluate('window.cspViolations')
        assert not errors, errors
        checks.append('Basemap toggling and attribution, 320/390px reflow, reduced motion, CSP')
        (output / 'verification.json').write_text(json.dumps({'status': 'passed', 'checks': checks, 'javascript_errors': errors,
            'accessibility': 'No automated WCAG A/AA violations in tested states; manual assistive-technology review remains required.'}, indent=2))
        await browser.close()
        print('React browser checks passed; artifacts/web-react-preview/')


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8001'))
