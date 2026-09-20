// Real web API and canvas rendering; only external map tiles use a fixture.
// node tests/fire-rendering-browser.mjs http://127.0.0.1:8000
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright-core';

const base = process.argv[2] || 'http://127.0.0.1:8000';
const output = '../artifacts/fire-rendering';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });

async function paintedPixels(page) {
  // Leaflet batches canvas clearing/drawing on the next animation frame.
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  return page.locator('.leaflet-overlay-pane canvas').evaluateAll(canvases => {
    let pixels = 0;
    for (const canvas of canvases) {
      const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      for (let i = 3; i < data.length; i += 4) if (data[i]) pixels++;
    }
    return pixels;
  });
}

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin === new URL(base).origin) return route.continue();
    if (url.hostname !== 'tile.openstreetmap.org') return route.abort();
    return route.fulfill({ contentType: 'image/png',
      body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64') });
  });
  await page.goto(base);
  await page.locator('#map[data-connection=online]').waitFor();
  await page.locator('.coordinates summary').click();
  await page.locator('#latitude').fill('46.8');
  await page.locator('#longitude').fill('-100.8');
  const seeded = page.waitForResponse(response => response.url().endsWith('/api/seed'));
  await page.locator('#coordinate-place').click();
  const seedResponse = await seeded, seed = await seedResponse.json();
  assert.equal(seedResponse.status(), 200, JSON.stringify(seed));
  assert.ok(seed.points.every(point => point.geometry?.type === 'Polygon'), 'The running API must provide grid footprints');
  await page.locator('#active-count').filter({ hasText: String(seed.active_count) }).waitFor();
  assert.ok(await paintedPixels(page) > 0, 'Active fire must paint the map, not just increment the counter');
  await page.screenshot({ path: `${output}/active.png` });
  await page.locator('#show-active').uncheck();
  assert.equal(await paintedPixels(page), 0, 'Hiding active fire removes its pixels');
  await page.locator('#show-active').check();
  assert.ok(await paintedPixels(page) > 0, 'Showing active fire restores its pixels');

  let frame = seed;
  for (let step = 0; step < 6 && !frame.burned_count; step++) {
    const stepped = page.waitForResponse(response => response.url().endsWith('/api/step'));
    await page.locator('#step').click();
    const response = await stepped;
    frame = await response.json();
    assert.equal(response.status(), 200, JSON.stringify(frame));
    await page.locator('#elapsed').filter({ hasText: `+${frame.elapsed_hours} hours` }).waitFor();
  }
  assert.ok(frame.burned_count > 0, 'The seed must eventually burn out');
  await page.locator('#show-active').uncheck();
  assert.ok(await paintedPixels(page) > 0, 'Burned fire must remain visible with active fire hidden');
  await page.screenshot({ path: `${output}/burned.png` });
  await page.locator('#show-burned').uncheck();
  assert.equal(await paintedPixels(page), 0, 'Hiding both fire layers removes their pixels');
  await page.locator('#show-burned').check();
  assert.ok(await paintedPixels(page) > 0, 'Showing burned fire restores its pixels');

  // Reproduce the stale Python process/new static assets mismatch after a reset.
  await page.locator('#reset').click();
  await page.route('**/api/seed', route => route.fulfill({ json: {
    ...seed, points: seed.points.map(({ geometry, ...point }) => point),
  } }));
  await page.locator('#coordinate-place').click();
  await page.locator('#status.error').filter({ hasText: 'Restart the server' }).waitFor();
  assert.equal(await page.locator('#active-count').innerText(), '0', 'An undrawable response must not replace the scenario');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ active_rendered: true, burned_rendered: true, layer_toggles: true,
    stale_server_explained: true, elapsed_hours: frame.elapsed_hours, burned_cells: frame.burned_count }));
} finally {
  await browser.close();
}
