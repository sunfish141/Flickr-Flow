"""Real polygon clicks and inspection; only external basemap images are stubbed."""
import asyncio
import base64
import json
from pathlib import Path
import sys

from playwright.async_api import async_playwright, expect


async def main(base_url, large_frame=None):
    output = Path('artifacts/polygon-inspection/browser')
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width':1440,'height':1100}, reduced_motion='reduce')
        errors, requests = [], []
        page.on('pageerror',lambda error: errors.append(str(error)))
        page.on('request',lambda request: requests.append(request.url) if '/api/' in request.url else None)
        pixel = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        await page.route('https://tile.openstreetmap.org/**',lambda route: route.fulfill(body=pixel,content_type='image/png'))
        await page.goto(base_url)
        await expect(page.locator('#local-region')).to_be_visible()
        await page.locator('#show-basemap').uncheck()
        results = []
        for weather_ml in (False, True):
            await page.locator('#local-region').select_option('alberta')
            await page.locator('#weather-ml').set_checked(weather_ml)
            if await page.locator('.coordinates').get_attribute('open') is None:
                await page.locator('.coordinates summary').click()
            async with page.expect_response('**/api/landscape/seed',timeout=180000) as pending:
                await page.locator('#coordinate-place').click()
            response = await pending.value
            initial = await response.json()
            assert response.status == 200,initial
            cell_id = initial['points'][0]['cell_id']
            await page.locator('#fit').click()
            square = page.locator(f'path.polygon-cell[data-cell-id="{cell_id}"]')
            await expect(square).to_be_visible()
            before = len([r for r in requests if r.endswith('/api/landscape/seed')])
            async with page.expect_response('**/api/vegetation',timeout=120000) as pending:
                await square.click()
            vegetation = await (await pending.value).json()
            assert vegetation['status'] == 'available'
            await expect(page.locator('#point-id')).to_have_text(cell_id)
            await expect(page.locator('#point-title')).to_be_focused()
            await expect(page.locator('#point-details')).to_contain_text('Active area')
            await expect(page.locator('#point-details')).to_contain_text('Burned area')
            await expect(page.locator('#point-details')).to_contain_text('Mapped road length')
            await expect(page.locator('#vegetation-details')).to_contain_text('Predominant land cover')
            await expect(page.locator('path.selected-polygon-cell')).to_have_count(1)
            assert await square.get_attribute('d') == await page.locator('path.selected-polygon-cell').get_attribute('d')
            assert len([r for r in requests if r.endswith('/api/landscape/seed')]) == before
            await page.screenshot(path=str(output/f'{"weather" if weather_ml else "standard"}-selected.png'))
            # Selection follows the cell through its transition to burned.
            for step in range(1,9):
                async with page.expect_response('**/api/landscape/step',timeout=180000) as pending:
                    await page.locator('#step').click()
                response = await pending.value
                frame = await response.json()
                assert response.status == 200,frame
                await expect(page.locator('#elapsed')).to_have_text(f'+{step*12} hours')
                await expect(page.locator('#point-id')).to_have_text(cell_id)
                await expect(page.locator('path.selected-polygon-cell')).to_have_count(1)
                selected = next(point for point in frame['points'] if point['cell_id'] == cell_id)
                if selected['status'] == 'burned':
                    break
            assert selected['status'] == 'burned','Selected cell did not burn out in the test range'
            await expect(page.locator('#point-title')).to_have_text('Burned cell')
            await page.locator('#show-burned').uncheck()
            await expect(square).to_have_count(0)
            await expect(page.locator('path.selected-polygon-cell')).to_have_count(0)
            await page.locator('#show-burned').check()
            await expect(square).to_be_visible()
            await expect(page.locator('path.selected-polygon-cell')).to_have_count(1)
            await page.locator('#timeline').fill('0')
            await expect(page.locator('#point-title')).to_have_text('Active fire')
            await expect(page.locator('path.selected-polygon-cell')).to_have_count(1)
            await page.locator('#close-inspector').click()
            await expect(page.locator('#map')).to_be_focused()
            await expect(page.locator('path.selected-polygon-cell')).to_have_count(0)
            if await page.locator('#cell-list').get_attribute('open') is None:
                await page.locator('#cell-list summary').click()
            await page.locator('#cell-search').fill(cell_id)
            button = page.locator('#cell-list li button').first
            await button.focus()
            await page.keyboard.press('Enter')
            await expect(page.locator('path.selected-polygon-cell')).to_have_count(1)
            await page.locator('#close-inspector').click()
            await expect(button).to_be_focused()
            results.append({'weather_ml':weather_ml,'cell_id':cell_id,'burnout_hours':step*12,
                            'map_click':True,'cell_list_highlight':True,'selection_survives_burnout_and_rewind':True})
            await page.locator('#reset').click()
        # Explicit overlay fixture: a historical marker must remain clickable
        # above a polygon square at the same location.
        overlay = json.loads(json.dumps(initial))
        overlay['historical'] = {'date':initial['origin_at'][:10], 'cell_count':1,'detection_count':3,
            'points':[{**initial['points'][0],'status':'historical','detection_count':3}]}
        await page.route('**/api/landscape/seed',lambda route: route.fulfill(json=overlay))
        await page.locator('#coordinate-place').click()
        await expect(page.locator('path[stroke="#6536b3"]')).to_be_visible()
        await page.locator('#fit').click()
        await page.locator('path[stroke="#6536b3"]').click()
        await expect(page.locator('#point-title')).to_have_text('Historical FIRMS')
        await expect(page.locator('path.selected-polygon-cell')).to_have_count(0)
        await page.locator('#close-inspector').click()
        square = page.locator('path.polygon-cell').first
        bounds = await square.bounding_box()
        await square.click(position={'x':bounds['width']*.2,'y':bounds['height']*.5})
        await expect(page.locator('#point-title')).to_have_text('Active fire')
        await expect(page.locator('path.selected-polygon-cell')).to_have_count(1)
        await page.unroute('**/api/landscape/seed')
        await page.locator('#reset').click()
        await page.set_viewport_size({'width':390,'height':844})
        await page.locator('#coordinate-place').click()
        await expect(page.locator('#active-count')).to_have_text('1',timeout=180000)
        await page.locator('#fit').click()
        await page.locator('path.polygon-cell').first.click()
        await expect(page.locator('#inspector')).to_be_visible()
        await expect(page.locator('#vegetation-details')).to_contain_text('Predominant land cover',timeout=120000)
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path=str(output/'mobile-selected.png'),full_page=True)
        large_result = None
        if large_frame:
            # A recorded real capacity run is a display fixture here; it does
            # not replace the real prediction/click checks above.
            fixture = json.loads(Path(large_frame).read_text())
            assert len(fixture['points']) >= 300
            assert all('cell_geometry' in point for point in fixture['points'])
            await page.set_viewport_size({'width':1440,'height':1100})
            await page.locator('#reset').click()
            await page.route('**/api/landscape/seed',lambda route: route.fulfill(json=fixture))
            await page.locator('#coordinate-place').click()
            await expect(page.locator('#elapsed')).to_have_text(f"+{fixture['elapsed_hours']} hours")
            await page.locator('#fit').click()
            await expect(page.locator('path.polygon-cell')).to_have_count(len(fixture['points']))
            # Select a cell near the view center, away from the overlay controls.
            lat = sum(p['latitude'] for p in fixture['points'])/len(fixture['points'])
            lon = sum(p['longitude'] for p in fixture['points'])/len(fixture['points'])
            target = min(fixture['points'],key=lambda p:(p['latitude']-lat)**2+(p['longitude']-lon)**2)
            await page.locator(f'path.polygon-cell[data-cell-id="{target["cell_id"]}"]').click()
            await expect(page.locator('#point-id')).to_have_text(target['cell_id'])
            await expect(page.locator('#point-details')).to_contain_text(f"{target['burned_area_m2']/10000:.2f} ha")
            await expect(page.locator('path.selected-polygon-cell')).to_have_count(1)
            await expect(page.locator('#vegetation-details')).to_contain_text('Predominant land cover',timeout=120000)
            await page.mouse.move(325,90)
            await page.screenshot(path=str(output/'large-selected.png'))
            large_result = {'fixture_source':'recorded real capacity run','cells':len(fixture['points']),
                            'all_cells_drawn':True,'map_click':True}
        assert not errors,errors
        report = {'status':'passed','source_mode':'real Alberta polygons and vegetation, real optional weather ML',
                  'scenarios':results,'large_display':large_result,'historical_overlap_fixture':True,
                  'mobile_width':390,'javascript_errors':errors}
        (output/'verification.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2))
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8000',
                     sys.argv[2] if len(sys.argv)>2 else None))
