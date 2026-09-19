"""Opt-in live NASA smoke test; supply coordinates near current detections.

Requires a running server with a valid FIRMS key, a model and landscape sources.
Satellite responses are real. Only external basemap images are intercepted.
"""
import argparse
import asyncio
import base64
import json
from pathlib import Path
from playwright.async_api import async_playwright, expect

async def main(base_url, latitude, longitude):
    output = Path('artifacts/live-firms')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'browser-verification.json').unlink(missing_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={'width': 1440, 'height': 1050}, reduced_motion='reduce')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        pixel = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        await page.route('https://tile.openstreetmap.org/**', lambda route: route.fulfill(body=pixel, content_type='image/png'))
        await page.goto(base_url)
        await expect(page.locator('#local-region')).to_be_visible()
        config = await (await page.request.get(base_url + '/api/config')).json()
        assert config['firms_configured']
        await page.locator('#show-basemap').uncheck()
        # Coarse placement focuses the map on the supplied observation area.
        await page.locator('.coordinates summary').click()
        await page.locator('#latitude').fill(str(latitude))
        await page.locator('#longitude').fill(str(longitude))
        async with page.expect_response('**/api/seed'):
            await page.locator('#coordinate-place').click()
        await expect(page.locator('#active-count')).to_have_text('1')
        await page.locator('#firms-tab').click()
        await expect(page.locator('#load-firms')).to_be_enabled()
        await expect(page.locator('#firms-panel')).not_to_contain_text('Satellite loading is unavailable')
        await page.locator('#firms-scope').select_option('view')
        async with page.expect_response('**/api/firms', timeout=180000) as pending:
            await page.locator('#load-firms').click()
        response = await pending.value
        coarse = await response.json()
        assert response.status == 200 and coarse['active_count'] > 0, coarse
        await expect(page.locator('#status')).to_contain_text('Loaded')
        await page.screenshot(path=str(output/'coarse-firms.png'))
        # Auto polygon selection preserves the fitted, narrow satellite view.
        await page.locator('#local-region').click()
        await page.locator('[role=option][data-value="auto"]').click()
        await asyncio.sleep(10.1)  # The server enforces ten seconds between new FIRMS queries.
        async with page.expect_response('**/api/landscape/firms', timeout=240000) as pending:
            await page.locator('#load-firms').click()
        response = await pending.value
        polygon = await response.json()
        assert response.status == 200, polygon
        assert polygon['expanding'] and polygon['active_count'] > 0 and polygon['perimeters']['features'], polygon
        await expect(page.locator('#status')).to_contain_text('Loaded satellite-seeded landscape scenario')
        await page.screenshot(path=str(output/'polygon-firms.png'))
        assert not errors, errors
        report = {'status': 'passed', 'live_provider': 'NASA FIRMS; no satellite responses stubbed',
                  'firms_configured': config['firms_configured'], 'coarse_active_cells': coarse['active_count'],
                  'coarse_metadata': coarse['metadata'], 'polygon_active_cells': polygon['active_count'],
                  'polygon_tiles': len(polygon['state']['tiles']), 'polygon_metadata': polygon['metadata'],
                  'javascript_errors': errors}
        (output/'browser-verification.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        await browser.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base_url', nargs='?', default='http://127.0.0.1:8000')
    parser.add_argument('--latitude', type=float, required=True)
    parser.add_argument('--longitude', type=float, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.base_url.rstrip('/'), args.latitude, args.longitude))
