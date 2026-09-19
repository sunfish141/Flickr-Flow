// Real prepared packs and the original web service must be available.
// node tests/web-polygon-browser.mjs http://127.0.0.1:8000
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright-core';

const base = process.argv[2] || 'http://127.0.0.1:8000';
const artifacts = '../artifacts/web-polygons';
await mkdir(artifacts, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const errors = [], requests = [], report = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.method() === 'POST') requests.push(new URL(request.url()).pathname); });
  // Don't download/cache public map tiles as part of an automated test.
  await page.route('https://tile.openstreetmap.org/**', route => route.fulfill({ contentType: 'image/png',
    body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64') }));
  await page.addInitScript(() => {
    window.mapDraws = { arc: 0, lineTo: 0 };
    for (const method of ['arc', 'lineTo']) {
      const original = CanvasRenderingContext2D.prototype[method];
      CanvasRenderingContext2D.prototype[method] = function (...args) {
        if (this.canvas.closest('#map')) window.mapDraws[method]++;
        return original.apply(this, args);
      };
    }
  });
  const configResponse = page.waitForResponse(response => response.url().endsWith('/api/config'));
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  const config = await (await configResponse).json();
  assert.equal(config.local_spread.default_region, 'black-hawk-colorado');
  assert.equal(config.local_spread.mesh_m, 100);
  await page.waitForFunction(() => document.querySelector('#local-region')?.value === 'black-hawk-colorado');
  assert.equal(await page.locator('#intensity').count(), 0, 'Unused grid intensity slider must not appear');
  await page.locator('details.coordinates summary').click();
  for (const region of config.local_spread.regions) {
    await page.locator('#local-region').click();
    await page.locator(`[role=option][data-value="${region.id}"]`).click();
    await page.waitForFunction(lat => Number(document.querySelector('#latitude').value) === lat, region.example_ignition.latitude);
    assert.match(await page.locator('#polygon-coverage-note').innerText(), /81 km²/);
    const seedResponse = page.waitForResponse(response => response.url().endsWith('/api/local/seed'), { timeout: 120000 });
    const started = performance.now();
    await page.locator('#coordinate-place').click();
    const seedHttp = await seedResponse, seed = await seedHttp.json();
    assert.equal(seedHttp.status(), 200, JSON.stringify(seed));
    assert.equal(seed.local, true);
    assert.match(await page.locator('#model-name').innerText(), /Polygon travel engine/);
    assert.equal(seed.state.region, region.id);
    assert.ok(seed.perimeters.features.length > 0);
    assert.equal(seed.coverage.geometry.type, 'Polygon');
    const stepResponse = page.waitForResponse(response => response.url().endsWith('/api/local/step'), { timeout: 120000 });
    await page.locator('#step').click();
    const stepHttp = await stepResponse, step = await stepHttp.json();
    assert.equal(stepHttp.status(), 200, JSON.stringify(step));
    assert.equal(step.elapsed_hours, 12);
    assert.equal(step.state.model_sha256, seed.state.model_sha256);
    assert.ok(step.active_area_m2 + step.burned_area_m2 > seed.active_area_m2);
    assert.ok(step.perimeters.features.every(f => ['Polygon', 'MultiPolygon'].includes(f.geometry.type)));
    await page.waitForFunction(() => document.querySelector('#elapsed').textContent === '+12 hours');
    await page.locator('#fit').click();
    await page.waitForTimeout(300);
    const draws = await page.evaluate(() => window.mapDraws);
    assert.ok(draws.lineTo > 0, 'Polygon paths must be drawn');
    assert.equal(draws.arc, 0, 'Local fire must not be rendered as circle markers');
    // Duplicate requests are deterministic; a region switch must not contaminate the cache.
    const replayHttp = await page.request.post(`${base}/api/local/step`, { data: { state: seed.state, origin_at: seed.origin_at } });
    assert.deepEqual(await replayHttp.json(), step);
    await page.screenshot({ path: `${artifacts}/${region.id}-12h.png`, fullPage: true });
    report.push({ region: region.id, mesh_m: config.local_spread.mesh_m, elapsed_hours: step.elapsed_hours,
      active_ha: step.active_area_m2 / 10000, burned_ha: step.burned_area_m2 / 10000,
      perimeter_types: step.perimeters.features.map(f => f.geometry.type), circle_draws: draws.arc,
      seed_and_step_seconds: (performance.now() - started) / 1000 });
  }
  await page.locator('#reset').click();
  await page.locator('#latitude').fill('40.7');
  await page.locator('#longitude').fill('-74');
  const outsideResponse = page.waitForResponse(response => response.url().endsWith('/api/local/seed'));
  await page.locator('#coordinate-place').click();
  assert.equal((await outsideResponse).status(), 422);
  await page.locator('#status.error').waitFor();
  assert.equal(await page.locator('#elapsed').innerText(), '+0 hours');
  assert.equal(requests.filter(path => path === '/api/seed' || path === '/api/step').length, 0, 'No grid-model fallback');
  assert.deepEqual(errors, []);
  await writeFile(`${artifacts}/report.json`, JSON.stringify({ report, out_of_coverage_rejected: true, errors }, null, 2));
  console.log(JSON.stringify(report, null, 2));
} finally { await browser.close(); }
