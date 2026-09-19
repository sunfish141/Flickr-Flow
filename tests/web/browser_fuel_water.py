"""Real Fort McMurray water and fuel playback; only basemap images are stubbed."""
import asyncio
import base64
import json
from pathlib import Path
import sys

from playwright.async_api import async_playwright, expect


async def main(base_url):
    output = Path('artifacts/fuel-water/browser')
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width':1440,'height':1050}, reduced_motion='reduce')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        pixel = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        await page.route('https://tile.openstreetmap.org/**', lambda route: route.fulfill(body=pixel, content_type='image/png'))
        await page.goto(base_url)
        config = await (await page.request.get(base_url+'/api/config')).json()
        assert 'nalcms' in config['transition']['water_barrier']
        assert config['transition']['fuel_policy']['kind'] == 'vegetation-duration-proxy/v1'
        for latitude, longitude in [(56.74,-111.4),(59.2,-109.5),(39.62,-106.06)]:
            response = await page.request.post(base_url+'/api/seed', data={'ignitions': [
                {'latitude':latitude,'longitude':longitude,'intensity':.7}]}, timeout=120000)
            assert response.status == 422, await response.text()
        await page.locator('#show-basemap').uncheck()
        await page.locator('.coordinates summary').click()
        await page.locator('#latitude').fill('56.74')
        await page.locator('#longitude').fill('-111.4')
        await page.locator('#coordinate-place').click()
        await expect(page.locator('#status')).to_contain_text('mapped land', timeout=120000)
        await expect(page.locator('#active-count')).to_have_text('0')
        await page.locator('#longitude').fill('-111.42')
        async with page.expect_response('**/api/seed', timeout=120000) as pending:
            await page.locator('#coordinate-place').click()
        response = await pending.value
        initial = await response.json()
        assert response.status == 200, initial
        seed = initial['points'][0]
        assert seed['fuel_basis'] == 'land cover and canopy density'
        assert 12 < seed['burn_duration_hours'] < 24
        await page.locator('#cell-list summary').click()
        await page.locator('#cell-list li button').first.click()
        await expect(page.locator('#point-details')).to_contain_text('Assigned burn duration')
        await expect(page.locator('#point-details')).to_contain_text(f"{seed['burn_duration_hours']:.1f} h")
        await page.screenshot(path=str(output/'fuel-inspector.png'))
        await page.locator('#close-inspector').click()
        frames = [initial]
        blocked = {'naea-1km:x=-916:y=2035', 'naea-1km:x=-915:y=2035',
                   'naea-1km:x=-916:y=2036', 'naea-1km:x=-914:y=2036',
                   'naea-1km:x=-915:y=2037', 'naea-1km:x=-914:y=2037'}
        for hour in (12,24,36,48):
            body = {'state':frames[-1]['state'], 'origin_at':initial['origin_at']}
            response = await page.request.post(base_url+'/api/step', data=body, timeout=120000)
            assert response.status == 200, await response.text()
            frame = await response.json()
            assert blocked.isdisjoint(point['cell_id'] for point in frame['points'])
            if hour >= 24:
                assert seed['cell_id'] in frame['state']['burned_cell_ids']
            replay = await page.request.post(base_url+'/api/step', data=body, timeout=120000)
            assert await replay.json() == frame
            frames.append(frame)
        assert not errors, errors
        (output/'frames.json').write_text(json.dumps(frames))
        report = {'status':'passed', 'water_locations':['Athabasca River at Fort McMurray','Lake Athabasca','Dillon Reservoir'],
                  'seed':seed['cell_id'], 'assigned_duration_hours':seed['burn_duration_hours'],
                  'frames_hours':[f['elapsed_hours'] for f in frames], 'blocked_river_cells':sorted(blocked),
                  'replay_equal':True, 'javascript_errors':errors}
        (output/'verification.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2))
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8000'))
