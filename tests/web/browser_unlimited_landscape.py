"""Real polygon playback through one week; only basemap images are stubbed."""
import asyncio
import base64
import json
from pathlib import Path
import sys
import time

from playwright.async_api import async_playwright, expect


async def main(base_url):
    output = Path('artifacts/unlimited-landscape/browser')
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width':1440,'height':1050}, reduced_motion='reduce')
        errors, requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requests.append(request.url) if request.url.endswith('/api/landscape/step') else None)
        pixel = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        await page.route('https://tile.openstreetmap.org/**', lambda route: route.fulfill(body=pixel,content_type='image/png'))
        await page.goto(base_url)
        config = await (await page.request.get(base_url+'/api/config')).json()
        assert config['local_spread']['max_steps'] is None
        await page.locator('#show-basemap').uncheck()
        await page.locator('#local-region').select_option('colorado')
        await page.locator('.coordinates summary').click()
        async with page.expect_response('**/api/landscape/seed', timeout=240000) as pending:
            await page.locator('#coordinate-place').click()
        response = await pending.value
        initial = await response.json()
        assert response.status == 200, initial
        frames, timings = [initial], []
        for step in range(1,15):
            start = time.monotonic()
            async with page.expect_response('**/api/landscape/step', timeout=240000) as pending:
                if step == 9:
                    await page.locator('#speed').select_option('1')
                    await page.locator('#play').click()
                else:
                    await page.locator('#step').click()
            response = await pending.value
            frame = await response.json()
            assert response.status == 200, frame
            await expect(page.locator('#elapsed')).to_have_text(f'+{step*12} hours')
            if step == 9:
                await page.locator('#play').click()
            assert not frame['finished']
            assert frame['state']['incident_id'] == initial['state']['incident_id']
            assert frame['state']['ignitions'] == initial['state']['ignitions']
            assert frame['burned_area_m2'] + 1e-6 >= frames[-1]['burned_area_m2']
            await expect(page.locator('#step')).to_be_enabled()
            timings.append({'hours':step*12,'seconds':time.monotonic()-start,
                            'tiles':len(frame['state']['tiles']),'active_patches':frame['active_patch_count'],
                            'burned_area_m2':frame['burned_area_m2']})
            frames.append(frame)
        await page.locator('#fit').click()
        await page.screenshot(path=str(output/'one-week.png'))
        await page.locator('#timeline').fill('8')
        await expect(page.locator('#elapsed')).to_have_text('+96 hours')
        count = len(requests)
        await page.locator('#play').click()
        await expect(page.locator('#elapsed')).to_have_text('+108 hours')
        await page.locator('#play').click()
        assert len(requests) == count, 'Saved frames must replay without a new model request'
        body = {'state':frames[8]['state'],'origin_at':initial['origin_at']}
        replay = await page.request.post(base_url+'/api/landscape/step',data=body,timeout=240000)
        assert replay.status == 200
        assert await replay.json() == frames[9]
        await page.locator('#help').click()
        await expect(page.locator('#help-dialog')).to_contain_text('Polygon playback has no duration cap')
        assert not errors, errors
        report = {'status':'passed','source_mode':'real Colorado roads and vegetation',
                  'elapsed_hours':168,'crossed_96_hours_with_play':True,'saved_replay_without_request':True,
                  'api_replay_equal':True,'timings':timings,'javascript_errors':errors}
        (output/'verification.json').write_text(json.dumps(report,indent=2))
        (output/'one-week-frame.json').write_text(json.dumps(frames[-1]))
        print(json.dumps(report,indent=2))
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8000'))
