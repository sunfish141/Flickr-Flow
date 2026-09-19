// Combined service, real bundles, outbound requests blocked by the test browser.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright-core';

const base = process.argv[2] || 'http://127.0.0.1:8001';
const artifacts = process.argv[3] || '../artifacts/desktop-smoke/browser';
await mkdir(artifacts, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1500, height: 1100 }, acceptDownloads: true, reducedMotion: 'reduce' });
const external = [], errors = [];
await context.route('**/*', route => {
  if (new URL(route.request().url()).origin !== new URL(base).origin) {
    external.push(route.request().url());
    // Online-mode test uses an in-memory image, never the public tile service.
    return route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64') });
  }
  return route.continue();
});
const page = await context.newPage();
page.on('pageerror', error => errors.push(error.message));
try {
  await page.goto(base);
  const explorer = page.frameLocator('#explorer'), planner = page.frameLocator('#planner');
  await explorer.locator('#local-region').waitFor();
  await page.getByRole('button', { name: 'Enable online data', exact: true }).waitFor();
  assert.equal(await explorer.locator('#local-region').getAttribute('value'), 'black-hawk-colorado');
  assert.equal(await explorer.locator('#show-basemap').isDisabled(), true);
  await explorer.locator('details.coordinates summary').click();
  const seedResponse = page.waitForResponse(r => r.url().endsWith('/api/local/seed'), { timeout: 120000 });
  await explorer.locator('#coordinate-place').click();
  const seed = await (await seedResponse).json();
  assert.ok(seed.local && seed.perimeters.features.length);
  const stepResponse = page.waitForResponse(r => r.url().endsWith('/api/local/step'), { timeout: 120000 });
  await explorer.locator('#step').click();
  const frame = await (await stepResponse).json();
  assert.equal(frame.elapsed_hours, 12);
  assert.ok(frame.perimeters.features.every(f => ['Polygon', 'MultiPolygon'].includes(f.geometry.type)));
  await explorer.locator('#fit').click();
  assert.deepEqual(external, [], 'Cold start and local simulation must not request external resources');
  await page.screenshot({ path: `${artifacts}/explorer-offline.png` });

  await page.getByRole('tab', { name: 'Saved scenarios', exact: true }).click();
  await planner.getByLabel('Prepared region').selectOption('black-hawk-colorado');
  await planner.getByRole('button', { name: 'New baseline', exact: true }).click();
  await planner.getByLabel('Case name').fill('Combined desktop smoke');
  await planner.getByRole('status').filter({ hasText: /^Saved$/ }).waitFor();
  const config = await (await page.request.get(`${base}/api/config`)).json();
  const ignition = config.local_spread.regions.find(r => r.id === 'black-hawk-colorado').example_ignition;
  await planner.getByLabel('Latitude', { exact: true }).fill(String(ignition.latitude));
  await planner.getByLabel('Longitude', { exact: true }).fill(String(ignition.longitude));
  await planner.getByRole('button', { name: 'Add coordinate ignition' }).click();
  await planner.getByRole('status').filter({ hasText: /^Saved$/ }).waitFor();
  const identity = await planner.getByLabel('Saved case').inputValue();
  const started = Date.now();
  await planner.getByRole('button', { name: 'Run scenario', exact: true }).click();
  await page.getByRole('tab', { name: 'Explorer', exact: true }).click();
  assert.equal(await explorer.locator('#elapsed').innerText(), '+12 hours', 'Switching tabs must retain Explorer');
  let scenario;
  for (let i = 0; i < 240; i++) {
    scenario = await (await page.request.get(`${base}/planning/api/scenarios/${identity}`)).json();
    if (scenario.status === 'failed') throw new Error(scenario.error);
    if (scenario.status === 'complete') break;
    await page.waitForTimeout(500);
  }
  assert.equal(scenario.status, 'complete');
  assert.equal(scenario.checkpoint, 24);
  const runSeconds = (Date.now() - started) / 1000;
  await page.getByRole('tab', { name: 'Saved scenarios', exact: true }).click();
  await page.waitForTimeout(1600);
  const download = page.waitForEvent('download');
  await planner.getByRole('link', { name: 'Export JSON', exact: true }).click();
  await (await download).saveAs(`${artifacts}/scenario.json`);
  await page.screenshot({ path: `${artifacts}/saved-scenarios.png` });
  assert.deepEqual(external, [], 'Saved scenarios and export must remain offline');
  await page.getByRole('tab', { name: 'Explorer', exact: true }).click();
  await page.getByRole('button', { name: 'Enable online data', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#explorer').contentDocument.querySelector('#show-basemap').disabled === false);
  await page.waitForTimeout(500);
  assert.ok(external.some(url => url.startsWith('https://tile.openstreetmap.org/')), 'Online mode should enable optional map tiles');
  assert.equal(await explorer.locator('#elapsed').innerText(), '+12 hours');
  await page.getByRole('button', { name: 'Use offline mode', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#explorer').contentDocument.querySelector('#show-basemap').disabled);
  assert.equal(await explorer.locator('#elapsed').innerText(), '+12 hours');
  // A cold reload recovers durable cases and does not turn online access back on.
  await page.reload();
  await page.getByRole('tab', { name: 'Saved scenarios', exact: true }).click();
  await planner.getByLabel('Saved case').waitFor();
  await page.waitForFunction(id => Array.from(document.querySelector('#planner').contentDocument.querySelectorAll('select')).some(select => select.value === id), identity);
  assert.equal(await planner.getByLabel('Saved case').inputValue(), identity);
  assert.deepEqual(errors, []);
  const report = { identity, checkpoint: scenario.checkpoint, planner_run_seconds: runSeconds,
    offline_requests: 0, polygons: true, tab_state_retained: true, exports: true, online_toggle: true, errors };
  await writeFile(`${artifacts}/report.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} finally { await browser.close(); }
