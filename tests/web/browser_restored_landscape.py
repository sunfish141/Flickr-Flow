"""Real-source vegetation and road/polygon browser verification.

Requires a running server with completed startup preparation. Only basemap
images are stubbed; vegetation, roads, perimeters and inference use local data.
"""
import asyncio
import base64
import json
from pathlib import Path
import sys

from playwright.async_api import async_playwright, expect


async def main(base_url):
    output = Path('artifacts/restored-landscape/browser')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'verification.json').unlink(missing_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={'width': 1440, 'height': 1050}, reduced_motion='reduce')
        errors, step_requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: step_requests.append(request.url) if request.url.endswith('/api/landscape/step') else None)
        pixel = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        await page.route('https://tile.openstreetmap.org/**', lambda route: route.fulfill(body=pixel, content_type='image/png'))
        await page.goto(base_url)
        await expect(page.locator('#local-region')).to_be_visible()
        await page.locator('#local-region').click()
        await expect(page.locator('[role=option][data-value="auto"]')).to_contain_text('Polygon spread')
        await expect(page.locator('[role=option][data-value="alberta"]')).to_have_count(1)
        await expect(page.locator('[role=option][data-value="colorado"]')).to_have_count(1)
        await page.locator('#local-region').press('Escape')
        await page.locator('#show-basemap').uncheck()
        await page.locator('.coordinates summary').click()
        await page.locator('#latitude').fill('40.015')
        await page.locator('#longitude').fill('-105.27')
        await page.locator('#coordinate-place').click()
        await expect(page.locator('#active-count')).to_have_text('1')
        await page.locator('#cell-list summary').click()
        async with page.expect_response('**/api/vegetation', timeout=120000) as response:
            await page.locator('#cell-list li button').first.click()
        vegetation = await (await response.value).json()
        assert vegetation['status'] == 'available' and vegetation['density_fraction'] is not None
        await expect(page.locator('#vegetation-details')).to_contain_text('Vegetation density')
        await expect(page.locator('#vegetation-details')).to_contain_text('Predominant land cover')
        await page.screenshot(path=str(output / 'vegetation.png'))
        await page.locator('#close-inspector').click()
        await page.locator('#local-region').click()
        await page.locator('[role=option][data-value="auto"]').click()
        await page.locator('#latitude').fill('53.02000501581695')
        await page.locator('#longitude').fill('-117.31008206448098')
        async with page.expect_response('**/api/landscape/seed', timeout=180000) as response:
            await page.locator('#coordinate-place').click()
        initial = await (await response.value).json()
        assert initial['expanding'] and initial['roads']['features'] and initial['perimeters']['features'], initial
        async with page.expect_response('**/api/landscape/step', timeout=180000) as response:
            await page.locator('#step').click()
        advanced = await (await response.value).json()
        assert len(advanced['state']['tiles']) > len(initial['state']['tiles']), advanced
        assert advanced['burned_area_m2'] > 0 and advanced['active_area_m2'] > 0
        await expect(page.locator('#elapsed')).to_have_text('+12 hours')
        await page.locator('#fit').click()
        canvas = page.locator('#map .leaflet-overlay-pane canvas')
        await expect(canvas).to_be_visible()
        assert await canvas.evaluate("node => node.getContext('2d').getImageData(0, 0, node.width, node.height).data.some((value, i) => i % 4 === 3 && value > 0)")
        await page.screenshot(path=str(output / 'polygons.png'))
        await page.locator('#timeline').fill('0')
        await expect(page.locator('#elapsed')).to_have_text('+0 hours')
        await page.locator('#speed').select_option('1')
        before = len(step_requests)
        await page.locator('#play').click()
        await expect(page.locator('#elapsed')).to_have_text('+12 hours')
        await page.locator('#play').click()
        assert len(step_requests) == before, 'Saved polygon frames must replay without another prediction'
        await page.set_viewport_size({'width': 390, 'height': 844})
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path=str(output / 'mobile.png'), full_page=True)
        assert not errors, errors
        report = {'status': 'passed', 'source_mode': 'real retained vegetation and road archives',
            'checks': ['canopy and land-cover inspector', 'regional and expanding mode choices',
                       'polygon perimeters and roads', '12-hour expansion', 'saved polygon timeline replay', '390px layout'],
            'canopy_density': vegetation['density_fraction'], 'mapped_roads': len(initial['roads']['features']),
            'seed_tiles': len(initial['state']['tiles']), 'advanced_tiles': len(advanced['state']['tiles']),
            'javascript_errors': errors}
        (output / 'verification.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8000'))
