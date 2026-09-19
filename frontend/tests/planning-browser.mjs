// Full browser acceptance with every non-loopback page request denied.
import assert from 'node:assert/strict';
import { writeFile, mkdir } from 'node:fs/promises';
import { chromium } from 'playwright-core';
import AxeBuilder from '@axe-core/playwright';

const [base, ignitionJson, artifactDir] = process.argv.slice(2);
const ignitions = JSON.parse(ignitionJson);
const browser = await chromium.launch({ headless: true, args: ['--disable-background-networking', '--disable-component-update'] });
const context = await browser.newContext({ viewport: { width: 1500, height: 1100 }, acceptDownloads: true });
const externalRequests = [], errors = [];
await context.route('**/*', route => {
  const url = new URL(route.request().url());
  if (url.origin !== new URL(base).origin) { externalRequests.push(url.href); return route.abort(); }
  return route.continue();
});
const page = await context.newPage();
page.on('pageerror', e => errors.push(e.message));
const get = async path => (await page.request.get(base + '/api/' + path)).json();
async function saved() { await page.getByRole('status').filter({ hasText: /^Saved$/ }).waitFor(); }
async function currentCase() {
  const id = await page.getByLabel('Saved case').inputValue();
  return get(`scenarios/${id}`);
}
async function completed(start = Date.now()) {
  while (Date.now() - start < 240_000) {
    const scenario = await currentCase();
    if (scenario.status === 'failed') throw new Error(scenario.error);
    if (scenario.status === 'complete') return { scenario, seconds: (Date.now()-start)/1000 };
    await page.waitForTimeout(500);
  }
  throw new Error('Pilot run exceeded four-minute test ceiling');
}
const runs = [];
try {
  await page.goto(base);
  await page.getByRole('heading', { name: 'Offline planning', exact: true }).waitFor();
  const config = await get('config');
  for (const pack of config.packs) {
    await page.getByLabel('Prepared region').selectOption(pack.id);
    await page.getByRole('button', { name: 'New baseline', exact: true }).click();
    await page.getByLabel('Case name').waitFor();
    // Wait for the new selection, not a still-mounted preceding case.
    await page.waitForFunction(id => document.querySelector('.map-heading h3')?.textContent?.includes(id), pack.label);
    await page.getByLabel('Case name').fill(`${pack.label} offline smoke`);
    await saved();
    // A clean tab must adopt the other tab's form together with its revision,
    // not merely borrow that revision while keeping stale inputs.
    const secondTab = await context.newPage();
    await secondTab.goto(base);
    await secondTab.getByLabel('Wind speed (m/s)').fill('1');
    await secondTab.getByRole('status').filter({ hasText: /^Saved$/ }).waitFor();
    for (let attempt = 0; attempt < 30 && await page.getByLabel('Wind speed (m/s)').inputValue() !== '1'; attempt++) await page.waitForTimeout(200);
    assert.equal(await page.getByLabel('Wind speed (m/s)').inputValue(), '1');
    await page.getByLabel('Wind FROM (°)').fill('90'); await saved();
    assert.equal((await currentCase()).definition.wind.speed_m_s, 1);
    await secondTab.close();
    await page.getByLabel('Wind speed (m/s)').fill('0');
    await page.getByLabel('Wind FROM (°)').fill('0'); await saved();
    await page.getByLabel('Latitude', { exact: true }).fill(String(ignitions[pack.id].latitude));
    await page.getByLabel('Longitude', { exact: true }).fill(String(ignitions[pack.id].longitude));
    await page.getByRole('button', { name: 'Add coordinate ignition' }).click();
    await saved();
    const before = await currentCase();
    await page.reload();
    await page.getByRole('button', { name: 'Run scenario', exact: true }).waitFor();
    assert.equal((await currentCase()).definition.origin_time, before.definition.origin_time);
    const baselineStarted = Date.now();
    await page.getByRole('button', { name: 'Run scenario', exact: true }).click();
    await page.waitForTimeout(200);
    await page.reload(); // A refresh must not cancel the durable calculation.
    await page.getByLabel('Saved case').waitFor();
    const baseline = await completed(baselineStarted);
    const originalFrames = await get(`scenarios/${baseline.scenario.id}/frames`);
    assert.equal(originalFrames.frames.length, 25);
    // Wait for polling to reveal the last checkpoint before cloning/playing.
    await page.waitForTimeout(1700);
    await page.getByRole('button', { name: 'Clone as variant' }).click();
    await page.getByLabel('Wind speed (m/s)').waitFor({ state: 'visible' });
    await page.waitForFunction(() => !document.querySelector('input[type=number]')?.disabled && !document.querySelector('fieldset')?.disabled);
    await page.getByLabel('Wind speed (m/s)').fill('3');
    await page.getByLabel('Wind FROM (°)').fill('270');
    await saved();
    const variantStarted = Date.now();
    await page.getByRole('button', { name: 'Run scenario', exact: true }).click();
    const variant = await completed(variantStarted);
    await page.waitForTimeout(1700);
    assert.deepEqual(await get(`scenarios/${baseline.scenario.id}/frames`), originalFrames);
    const slider = page.getByRole('slider', { name: 'Synchronized elapsed hour' });
    await saved();
    // Hold an acknowledged older seek in flight while the user seeks again.
    // Its late response must not label the newer unsaved position "Saved".
    let releaseOld, releaseNew, oldArrived, newArrived;
    const oldHeld = new Promise(resolve => { releaseOld = resolve; });
    const newHeld = new Promise(resolve => { releaseNew = resolve; });
    const sawOld = new Promise(resolve => { oldArrived = resolve; });
    const sawNew = new Promise(resolve => { newArrived = resolve; });
    const playbackRoute = '**/api/scenarios/*/playback';
    await page.route(playbackRoute, async route => {
      const response = await route.fetch();
      const hour = route.request().postDataJSON().playback_hour;
      if (hour === 9) { oldArrived(); await oldHeld; }
      if (hour === 6) { newArrived(); await newHeld; }
      await route.fulfill({ response });
    });
    const arrived = promise => Promise.race([promise, new Promise((_, reject) => {
      const timer = setTimeout(() => reject(new Error('Playback save was dropped while another save was in flight')), 15_000);
      timer.unref();
    })]);
    await slider.fill('9');
    await arrived(sawOld);
    await slider.fill('6');
    assert.notEqual(await page.getByRole('status').textContent(), 'Saved');
    releaseOld(); await arrived(sawNew);
    assert.notEqual(await page.getByRole('status').textContent(), 'Saved');
    releaseNew(); await saved();
    await page.unroute(playbackRoute);
    assert.equal((await currentCase()).playback_hour, 6);
    await page.reload();
    await slider.waitFor();
    assert.equal(await slider.inputValue(), '6');
    assert.equal(await page.getByRole('button', { name: 'Play saved frames' }).count(), 1);
    // Reload intentionally restores one selected case; compare baseline again.
    await page.getByLabel('Comparison case').selectOption(baseline.scenario.id);
    await page.locator('.maps.split').waitFor();
    assert.equal(await page.locator('.planning-map').count(), 2);
    await mkdir(artifactDir, { recursive: true });
    await page.screenshot({ path: `${artifactDir}/${pack.id}.png`, fullPage: true });
    for (const format of ['JSON', 'HTML', 'GEOJSON']) {
      const downloaded = page.waitForEvent('download');
      await page.getByRole('link', { name: `Export ${format}`, exact: true }).click();
      const download = await downloaded;
      await download.saveAs(`${artifactDir}/${pack.id}.${format.toLowerCase()}`);
    }
    const accessibility = await new AxeBuilder({ page }).disableRules(['color-contrast']).analyze();
    assert.deepEqual(accessibility.violations.map(v => ({ id: v.id, impact: v.impact })), []);
    runs.push({ pack: pack.id, baseline_seconds: baseline.seconds, variant_seconds: variant.seconds,
      baseline_id: baseline.scenario.id, variant_id: variant.scenario.id, frames: 25 });
    console.log(JSON.stringify(runs.at(-1)));
    await page.getByLabel('Comparison case').selectOption('');
  }
  assert.deepEqual(externalRequests, []);
  assert.deepEqual(errors, []);
  await writeFile(`${artifactDir}/browser-report.json`, JSON.stringify({ runs, externalRequests, errors,
    browser: browser.version(), network_policy: 'non-loopback page requests aborted; server outbound audit guard enabled',
    accessibility: 'axe rules checked except color contrast; visual contrast requires separate review' }, null, 2));
} catch (error) {
  await mkdir(artifactDir, { recursive: true });
  await page.screenshot({ path: `${artifactDir}/failure.png`, fullPage: true });
  await writeFile(`${artifactDir}/failure.txt`, JSON.stringify({ error: String(error), errors, body: await page.locator('body').innerText() }, null, 2));
  throw error;
} finally { await browser.close(); }
