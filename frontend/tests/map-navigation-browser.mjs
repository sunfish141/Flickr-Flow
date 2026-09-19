// Run against the original web app, not the offline planner:
// node tests/map-navigation-browser.mjs http://127.0.0.1:8000
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright-core';

const base = process.argv[2] || 'http://127.0.0.1:8000';
const artifacts = '../artifacts/web-navigation';
const browser = await chromium.launch({ headless: true });
const reports = [];
await mkdir(artifacts, { recursive: true });

// Read geographic center from public rendered tile positions, with no Leaflet
// instance/test-only app hooks. Local image fixtures avoid external tile traffic.
async function view(page) {
  return page.evaluate(() => {
    const map = document.querySelector('#map').getBoundingClientRect();
    const tile = document.querySelector('.leaflet-tile');
    const [, z, x, y] = new URL(tile.src).pathname.match(/\/(\d+)\/(\d+)\/(\d+)\.png$/).map(Number);
    const rect = tile.getBoundingClientRect(), world = 256 * 2 ** z;
    const px = x * 256 + map.x + map.width / 2 - rect.x;
    const py = y * 256 + map.y + map.height / 2 - rect.y;
    return { zoom: z, longitude: ((px / world * 360) % 360 + 360) % 360 - 180,
      latitude: Math.atan(Math.sinh(Math.PI * (1 - 2 * py / world))) * 180 / Math.PI };
  });
}

async function drag(page, dx, dy) {
  await page.locator('#map').scrollIntoViewIfNeeded();
  const b = await page.locator('#map').boundingBox();
  const x = b.x + b.width * .6, y = b.y + Math.min(320, b.height * .65);
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx, y + dy, { steps: 20 });
  await page.mouse.up();
  await page.waitForTimeout(1000); // Include any bounds snap-back/inertia.
}

try {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
    const page = await browser.newPage({ viewport, reducedMotion: 'reduce' });
    const errors = [], seeds = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (/\/api\/(?:local\/|landscape\/)?seed$/.test(request.url())) seeds.push(request.url()); });
    await page.route('https://tile.openstreetmap.org/**', route => route.fulfill({ contentType: 'image/png',
      body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64') }));
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await page.locator('.leaflet-tile').first().waitFor({ state: 'attached' });
    await page.waitForTimeout(300);
    // The old viewport bounds prevent southward movement at this overview zoom.
    while ((await view(page)).zoom > 3) await page.getByRole('button', { name: 'Zoom out', exact: true }).click();
    while ((await view(page)).zoom < 3) await page.getByRole('button', { name: 'Zoom in', exact: true }).click();
    await page.locator('#map').scrollIntoViewIfNeeded();
    const before = await view(page);
    await drag(page, 0, -110);
    const south = await view(page);
    assert.ok(south.latitude < before.latitude - 5, `Southward drag snapped back: ${JSON.stringify({ before, south })}`);
    await drag(page, -90, 0);
    const east = await view(page);
    assert.ok(Math.abs(east.longitude - south.longitude) > 5, 'East/west panning is constrained');
    await page.getByRole('button', { name: 'US overview', exact: true }).click();
    await page.locator('#map').scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
    const us = await view(page);
    assert.ok(us.latitude > 30 && us.latitude < 45 && us.longitude > -105 && us.longitude < -85,
      `US overview did not center the continental US: ${JSON.stringify(us)}`);
    await page.locator('#place').click();
    await drag(page, 40, -45);
    assert.equal(seeds.length, 0, 'Dragging in placement mode must not ignite a fire');
    await page.screenshot({ path: `${artifacts}/navigation-${viewport.width}.png`, fullPage: true });
    assert.deepEqual(errors, []);
    reports.push({ viewport, before, south, east, us, drag_did_not_ignite: true });
    await page.close();
  }
  await writeFile(`${artifacts}/report.json`, JSON.stringify(reports, null, 2));
  console.log(JSON.stringify(reports, null, 2));
} finally { await browser.close(); }
