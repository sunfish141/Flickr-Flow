"""Real regional polygon runs plus isolated FIRMS request-routing checks.

The seed/step responses use the retained national archives. Basemap images and
the explicitly marked FIRMS control probes are intercepted (no NASA requests).
"""
import asyncio
import base64
import json
from pathlib import Path
import sys

from playwright.async_api import async_playwright, expect


async def main(base_url):
    output = Path('artifacts/regional-landscape/browser')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'verification.json').unlink(missing_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={'width': 1440, 'height': 1050}, reduced_motion='reduce')
        errors, seeds = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: seeds.append(request.url) if request.url.endswith('/seed') else None)
        pixel = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        await page.route('https://tile.openstreetmap.org/**', lambda route: route.fulfill(body=pixel, content_type='image/png'))
        await page.goto(base_url)
        config = await (await page.request.get(f'{base_url}/api/config')).json()
        presets = {p['id']: p for p in config['local_spread']['presets']}
        assert set(presets) == {'alberta', 'colorado'}
        await expect(page.locator('#local-region')).to_be_visible()
        await expect(page.locator('#local-region option[value="boulder"]')).to_have_count(0)
        await expect(page.locator('#local-region option[value="edson"]')).to_have_count(0)
        await page.locator('#show-basemap').uncheck()
        await page.locator('.coordinates summary').click()
        results = []
        for region, pilot in [('colorado', 'boulder'), ('alberta', 'edson')]:
            preset = presets[region]
            example = preset['example_ignition']
            old = next(r for r in config['local_spread']['regions'] if r['id'] == pilot)
            w, s, e, n = old['bounds']
            assert not (w <= example['longitude'] <= e and s <= example['latitude'] <= n)
            await page.locator('#local-region').select_option(region)
            await expect(page.locator('#active-count')).to_have_text('0')
            await expect(page.locator('#elapsed')).to_have_text('+0 hours')
            await expect(page.locator('.map-title')).to_have_text(f'{region.upper()} · POLYGON SPREAD')
            await expect(page.locator('#latitude')).to_have_value(str(example['latitude']))
            await expect(page.locator('#longitude')).to_have_value(str(example['longitude']))
            await page.screenshot(path=str(output / f'{region}-view.png'))
            async with page.expect_response('**/api/landscape/seed', timeout=240000) as pending:
                await page.locator('#coordinate-place').click()
            response = await pending.value
            initial = await response.json()
            assert response.status == 200, initial
            assert initial['expanding'] and initial['roads']['features'] and initial['perimeters']['features']
            assert len(initial['state']['tiles']) <= 24
            async with page.expect_response('**/api/landscape/step', timeout=240000) as pending:
                await page.locator('#step').click()
            response = await pending.value
            advanced = await response.json()
            assert response.status == 200, advanced
            assert advanced['state']['incident_id'] == initial['state']['incident_id']
            assert advanced['burned_area_m2'] > 0
            await expect(page.locator('#elapsed')).to_have_text('+12 hours')
            await page.locator('#fit').click()
            canvas = page.locator('#map .leaflet-overlay-pane canvas')
            await expect(canvas).to_be_visible()
            assert await canvas.evaluate("node => node.getContext('2d').getImageData(0, 0, node.width, node.height).data.some((value, i) => i % 4 === 3 && value > 0)")
            await page.screenshot(path=str(output / f'{region}-fire.png'))
            results.append({'region': region, 'ignition': example, 'outside_old_pilot': True,
                            'roads': len(initial['roads']['features']), 'seed_tiles': len(initial['state']['tiles']),
                            'advanced_tiles': len(advanced['state']['tiles']), 'burned_area_m2': advanced['burned_area_m2']})
            (output / f'{region}-seed.json').write_text(json.dumps(initial))
            (output / f'{region}-step.json').write_text(json.dumps(advanced))
        assert all(url.endswith('/api/landscape/seed') for url in seeds), seeds

        # Isolated UI probes: enable unavailable FIRMS controls but intercept
        # their requests. Assert actual map bounds and endpoint selection.
        fixtures = []
        control_config = {**config, 'firms_configured': True,
                          'historical_firms': {**config['historical_firms'], 'available': True}}
        await page.route('**/api/config', lambda route: route.fulfill(json=control_config))

        async def reject_firms(route):
            fixtures.append({'url': route.request.url, 'body': route.request.post_data_json})
            await route.fulfill(status=422, json={'detail': 'Regional control probe; no satellite data loaded.'})

        await page.route('**/api/landscape/firms', reject_firms)
        await page.route('**/api/landscape/firms/historical', reject_firms)
        await page.reload()
        await expect(page.locator('#local-region')).to_be_visible()
        await page.locator('#show-basemap').uncheck()
        for region in ['colorado', 'alberta']:
            await page.locator('#place-tab').click()
            await page.locator('#local-region').select_option(region)
            await page.locator('#firms-tab').click()
            await expect(page.locator('#local-region')).to_have_value(region)
            await expect(page.locator('#firms-scope')).to_have_value('view')
            await expect(page.locator('#firms-scope option[value="all"]')).to_have_js_property('disabled', True)
            for historical in [False, True]:
                await page.locator('#historical-mode').set_checked(historical)
                if historical:
                    await page.locator('#historical-date').fill('2026-05-11')
                endpoint = 'firms/historical' if historical else 'firms'
                async with page.expect_response(f'**/api/landscape/{endpoint}'):
                    await page.locator('#load-firms').click()
                await expect(page.locator('#status')).to_contain_text('Regional control probe')
                bounds = fixtures[-1]['body']['bounds'] if historical else fixtures[-1]['body']
                w, s, e, n = presets[region]['bounds']
                assert bounds['west'] <= w and bounds['south'] <= s and bounds['east'] >= e and bounds['north'] >= n, bounds
                assert bounds['east'] - bounds['west'] < 70, bounds
            await page.locator('#place-tab').click()
            await expect(page.locator('#local-region')).to_have_value(region)
        await page.set_viewport_size({'width': 390, 'height': 844})
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path=str(output / 'mobile.png'), full_page=True)
        assert not errors, errors
        report = {'status': 'passed', 'real_source_checks': results,
                  'fixture_checks': ['regional map bounds', 'regional selection retained across source tabs',
                                     'visible-area FIRMS only', 'live and historical landscape routing'],
                  'firms_control_probes': len(fixtures), 'javascript_errors': errors}
        (output / 'verification.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8000'))
