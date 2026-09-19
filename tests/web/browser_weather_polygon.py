"""Real fitted weather ML, native landscapes and Open-Meteo; basemap stub only."""
import asyncio
import base64
import json
from pathlib import Path
import sys
import time

from playwright.async_api import async_playwright, expect


async def main(base_url):
    output=Path('artifacts/weather-polygon/browser')
    output.mkdir(parents=True,exist_ok=True)
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(viewport={'width':1440,'height':1100},reduced_motion='reduce')
        errors,requests=[],[]
        page.on('pageerror',lambda error: errors.append(str(error)))
        page.on('request',lambda req: requests.append(req.url) if req.url.endswith('/api/landscape/step') else None)
        pixel=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        await page.route('https://tile.openstreetmap.org/**',lambda route: route.fulfill(body=pixel,content_type='image/png'))
        await page.goto(base_url)
        config=await (await page.request.get(base_url+'/api/config')).json()
        assert config['local_spread']['weather_ml']['available'],config['local_spread']['weather_ml']
        await page.locator('#show-basemap').uncheck()
        report=[]
        for region in ['colorado','alberta']:
            await page.locator('#local-region').select_option(region)
            await page.locator('#weather-ml').check()
            for mode in ['forecast','historical']:
                await page.locator('#weather-date').fill('2026-08-20' if mode=='historical' else '')
                if await page.locator('.coordinates').get_attribute('open') is None:
                    await page.locator('.coordinates summary').click()
                start=time.monotonic()
                async with page.expect_response('**/api/landscape/seed',timeout=240000) as pending:
                    await page.locator('#coordinate-place').click()
                response=await pending.value
                initial=await response.json()
                assert response.status==200,initial
                assert initial['weather_ml']
                weather=initial['metadata']['hybrid']['weather']
                assert weather['mode']==mode,weather
                await expect(page.locator('#weather-summary')).to_contain_text('Historical analysis' if mode=='historical' else 'Captured forecast')
                frames=[initial]
                for step in range(1,6 if mode=='historical' else 4):
                    async with page.expect_response('**/api/landscape/step',timeout=240000) as pending:
                        await page.locator('#step').click()
                    response=await pending.value
                    frame=await response.json()
                    assert response.status==200,frame
                    assert frame['state']['weather_snapshot']==initial['state']['weather_snapshot']
                    assert frame['state']['incident_id']==initial['state']['incident_id']
                    assert frame['burned_area_m2']+1e-6 >= frames[-1]['burned_area_m2']
                    await expect(page.locator('#elapsed')).to_have_text(f'+{step*12} hours')
                    frames.append(frame)
                if mode=='historical':
                    assert frames[-1]['finished']
                    await expect(page.locator('#step')).to_be_disabled()
                await page.locator('#fit').click()
                await page.screenshot(path=str(output/f'{region}-{mode}.png'))
                count=len(requests)
                await page.locator('#timeline').fill('1')
                await expect(page.locator('#elapsed')).to_have_text('+12 hours')
                await page.locator('#step').click()
                await expect(page.locator('#elapsed')).to_have_text('+24 hours')
                assert len(requests)==count
                repeated=await page.request.post(base_url+'/api/landscape/step',data={
                    'state':frames[1]['state'],'origin_at':initial['origin_at']},timeout=240000)
                assert repeated.status==200
                assert await repeated.json()==frames[2]
                report.append({'region':region,'mode':mode,'seconds':time.monotonic()-start,
                    'elapsed_hours':frames[-1]['elapsed_hours'],'active_patches':frames[-1]['active_patch_count'],
                    'burned_area_m2':frames[-1]['burned_area_m2'],'tiles':len(frames[-1]['state']['tiles']),
                    'weather':weather,'api_replay_equal':True,'saved_replay_without_request':True})
                (output/f'{region}-{mode}-frame.json').write_text(json.dumps(frames[-1]))
                await page.locator('#reset').click()
        await page.locator('#help').click()
        await expect(page.locator('#help-dialog')).to_contain_text('Open-Meteo')
        assert not errors,errors
        result={'status':'passed','scenarios':report,'javascript_errors':errors,'source_mode':'real model, roads, vegetation, forecast and historical weather'}
        (output/'verification.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2))
        await browser.close()


if __name__=='__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8000'))
