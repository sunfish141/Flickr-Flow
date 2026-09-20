// Real packs/inference. All external traffic is blocked or fixture-intercepted.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright-core';

const base = process.argv[2] || 'http://127.0.0.1:8002';
const output = process.argv[3] || '../artifacts/unified-map-browser';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
const external = [], errors = [], posts = [];
await context.route('**/*', route => {
  if (new URL(route.request().url()).origin !== new URL(base).origin) { external.push(route.request().url()); return route.abort(); }
  return route.continue();
});
const page = await context.newPage();
page.on('pageerror', e => errors.push(e.message));
page.on('request', r => { if (r.method() === 'POST') posts.push({ path: new URL(r.url()).pathname, body: r.postDataJSON() }); });
try {
  await page.goto(base);
  const explorer = page.frameLocator('#explorer');
  await explorer.locator('#map[data-overview=ready]').waitFor();
  const config = await (await page.request.get(`${base}/api/config`)).json();
  if (config.local_spread.regions.some(r => r.tiled)) {
    await explorer.locator('#map[data-regional-map=ready]').waitFor();
    assert.equal(await explorer.locator('.leaflet-regionalMaps-pane img').evaluateAll(images =>
      images.length > 0 && images.every(image => image.complete && image.naturalWidth === 256)), true);
  }
  assert.equal(await page.locator('#planner,#planning-tab,#network-toggle').count(), 0);
  assert.equal(await explorer.locator('#local-region,#intensity').count(), 0);
  assert.equal(await explorer.locator('.installed-boundary').count(), 2);
  assert.equal(await explorer.locator('.installed-map-label button').count(), 2);
  assert.equal(await explorer.locator('#installed-regions button[data-region]').count(), 2);
  assert.equal(await explorer.locator('#map-mode').innerText(), 'Offline overview');
  assert.equal(await explorer.locator('.leaflet-offlineOverview-pane canvas').evaluate(canvas =>
    canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data.some((v, i) => i % 4 === 3 && v > 0)), true);
  await page.screenshot({ path: `${output}/overview.png` });
  await explorer.locator('.coordinates summary').click();
  const regionReports = [];
  for (const region of config.local_spread.regions) {
    const seedPath = region.tiled ? '/api/landscape/seed' : '/api/map/seed';
    const stepPath = region.tiled ? '/api/landscape/step' : '/api/local/step';
    const layersResponse = await page.request.get(`${base}${region.tiled ? region.map_tiles.replace('{z}', '0').replace('{x}', '0').replace('{y}', '0') : `/api/local/regions/${region.id}/layers`}`);
    assert.equal(layersResponse.status(), 200, 'Installed map layers must load offline');
    const localLayers = region.tiled ? null : await layersResponse.json();
    if (localLayers) {
      assert.equal(localLayers.id, region.id);
      assert.ok(localLayers.layers.cover.features.length, 'Bundled land cover must not be empty');
      assert.ok(localLayers.layers.roads.features.length, 'Bundled roads must not be empty');
    }
    await explorer.locator(`#installed-regions button[data-region="${region.id}"]`).click();
    if (await explorer.locator('#reset').isEnabled()) await explorer.locator('#reset').click();
    await explorer.locator('#latitude').fill(String(region.example_ignition.latitude));
    await explorer.locator('#longitude').fill(String(region.example_ignition.longitude));
    const seeded = page.waitForResponse(r => r.url().endsWith(seedPath));
    await explorer.locator('#coordinate-place').click();
    const seedResponse = await seeded, seed = await seedResponse.json();
    assert.equal(seedResponse.status(), 200, JSON.stringify(seed));
    assert.equal(seed.region_id || seed.state.region, region.id);
    assert.ok(seed.perimeters.features.length);
    await explorer.locator('#status').waitFor({ state: 'hidden' });
    assert.ok(!Object.hasOwn(posts.findLast(p => p.path === seedPath).body, 'region'));
    // Coordinate placement centres the map at a known supported fuel patch.
    // Reset without navigating, then exercise the real Leaflet click path too.
    await explorer.locator('#reset').click();
    await explorer.locator('#status').waitFor({ state: 'hidden' });
    await explorer.locator('#place').click();
    assert.equal(await explorer.locator('#map').evaluate(map => map.classList.contains('leaflet-container')), true, 'Placement must preserve Leaflet layout classes');
    const clickedSeed = page.waitForResponse(r => r.url().endsWith(seedPath));
    const mapBox = await explorer.locator('#map').boundingBox();
    await page.mouse.click(mapBox.x + mapBox.width / 2, mapBox.y + mapBox.height / 2);
    const clickedResponse = await clickedSeed;
    assert.equal(clickedResponse.status(), 200, await clickedResponse.text());
    const clicked = await clickedResponse.json();
    assert.equal(clicked.region_id || clicked.state.region, region.id);
    await explorer.locator('#place').click();
    const stepped = page.waitForResponse(r => r.url().endsWith(stepPath));
    await explorer.locator('#step').click();
    const stepResponse = await stepped, step = await stepResponse.json();
    assert.equal(stepResponse.status(), 200);
    assert.equal(step.elapsed_hours, 12);
    assert.ok(step.perimeters.features.every(f => ['Polygon', 'MultiPolygon'].includes(f.geometry.type)));
    await explorer.locator('#elapsed').filter({ hasText: '+12 hours' }).waitFor();
    await explorer.locator('#status').waitFor({ state: 'hidden' });
    assert.equal(await explorer.locator('#boundary-warning').count(), step.boundary_reached ? 1 : 0);
    // The merged upstream inspection layer must work with the unified map,
    // not just with its former simulator-selection UI.
    const playback = await explorer.locator('.playback').boundingBox();
    const mapBounds = await explorer.locator('#map').boundingBox();
    let inspected = false;
    for (const cell of await explorer.locator('.polygon-cell').all()) {
      const box = await cell.boundingBox();
      if (!box) continue;
      const x = box.x + box.width / 2, y = box.y + box.height / 2;
      if (x < mapBounds.x + 100 || x > mapBounds.x + mapBounds.width - 120 || y < mapBounds.y + 170 || y > playback.y - 10) continue;
      await page.mouse.click(x, y);
      await explorer.locator('#inspector').waitFor();
      assert.ok(await explorer.locator('.selected-polygon-cell').count());
      await explorer.locator('#close-inspector').click();
      inspected = true; break;
    }
    assert.ok(inspected, 'A visible polygon summary cell should be inspectable');
    await page.screenshot({ path: `${output}/${region.id}.png` });
    const other = config.local_spread.regions.find(r => r.id !== region.id);
    await explorer.locator(`#installed-regions button[data-region="${other.id}"]`).click();
    assert.equal(await explorer.locator('#elapsed').innerText(), '+12 hours', 'Browsing another region must not reset the simulation');
    regionReports.push({ region: region.id, elapsed_hours: step.elapsed_hours, polygonFeatures: step.perimeters.features.length, map_click: true,
      offline_map_tiles: !!region.tiled, offline_cover_features: localLayers?.layers.cover.features.length, offline_road_features: localLayers?.layers.roads.features.length });
  }
  await explorer.locator('#reset').click();
  await explorer.locator('#latitude').fill('40.7'); await explorer.locator('#longitude').fill('-74');
  const calls = posts.length;
  await explorer.locator('#coordinate-place').click();
  await explorer.locator('#status.error').filter({ hasText: 'not installed here' }).waitFor();
  assert.equal(posts.length, calls, 'Uninstalled locations must not call the coarse simulator');
  assert.equal(posts.filter(p => p.path === '/api/seed' || p.path === '/api/step').length, 0);
  assert.deepEqual(external, [], 'Forced-offline cold start must make no external requests');

  const layouts = [];
  for (const [width, height] of [[1440, 900], [1280, 720], [1024, 650], [853, 600], [750, 800], [390, 844]]) {
    await page.setViewportSize({ width, height });
    await page.waitForTimeout(120);
    const metrics = await explorer.locator('body').evaluate(body => {
      const map = document.querySelector('.map-workspace'), sidebar = document.querySelector('.sidebar');
      const playback = document.querySelector('.playback').getBoundingClientRect();
      return { pageOverflowX: document.documentElement.scrollWidth > innerWidth,
        pageOverflowY: innerWidth > 750 && document.documentElement.scrollHeight > innerHeight + 1,
        sidebarOverflowX: sidebar.scrollWidth > sidebar.clientWidth,
        playbackOutsideMap: playback.right > map.getBoundingClientRect().right + 1 || playback.left < map.getBoundingClientRect().left - 1,
        playbackBelowViewport: innerWidth > 750 && playback.bottom > innerHeight + 1 };
    });
    if (Object.values(metrics).some(Boolean)) {
      await page.screenshot({ path: `${output}/overflow-${width}.png` });
      console.log(await explorer.locator('body').evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth,
        map: { overflow: getComputedStyle(document.querySelector('#map')).overflow, width: document.querySelector('#map').clientWidth,
          right: document.querySelector('#map').getBoundingClientRect().right, position: getComputedStyle(document.querySelector('#map')).position },
        offenders: [...document.querySelectorAll('body *')].filter(n => n.getBoundingClientRect().right > innerWidth).slice(0, 15)
          .map(n => ({ tag: n.tagName, class: String(n.className), id: n.id, right: n.getBoundingClientRect().right })) })));
    }
    assert.ok(Object.values(metrics).every(v => !v), JSON.stringify({ width, height, metrics }));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth || document.documentElement.scrollHeight > innerHeight + 1), false);
    await page.screenshot({ path: `${output}/layout-${width}.png` });
    layouts.push({ width, height, ...metrics });
  }

  // Separate isolated web document verifies actual tile load/error callbacks,
  // link events and retry without changing the forced-offline service policy.
  const online = await browser.newContext({ viewport: { width: 1280, height: 800 }, reducedMotion: 'reduce' });
  let tilesWork = true, tileRequests = 0;
  const pixel = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64');
  await online.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== new URL(base).origin) {
      if (url.hostname !== 'tile.openstreetmap.org') return route.abort();
      tileRequests++;
      return tilesWork ? route.fulfill({ contentType: 'image/png', body: pixel }) : route.abort();
    }
    if (url.pathname === '/api/config') return route.fulfill({ json: { ...config, desktop: null } });
    return route.continue();
  });
  const auto = await online.newPage();
  auto.on('pageerror', e => errors.push(e.message));
  await auto.clock.install();
  await auto.goto(base + '/explore/');
  await auto.locator('#map[data-connection=online]').waitFor();
  // This is the reported regression: connected placement outside either pack
  // must reach the existing model, and must disclose its coarser resolution.
  await auto.locator('.coordinates summary').click();
  await auto.locator('#latitude').fill('46.8');
  await auto.locator('#longitude').fill('-100.8');
  const coarseSeeded = auto.waitForResponse(r => r.url().endsWith('/api/seed'));
  await auto.locator('#coordinate-place').click();
  const coarseResponse = await coarseSeeded, coarse = await coarseResponse.json();
  assert.equal(coarseResponse.status(), 200, JSON.stringify(coarse));
  assert.equal(coarse.simulation.resolution_m, 1000);
  assert.ok(coarse.points.every(p => p.geometry.type === 'Polygon'));
  await auto.locator('#map[data-simulation=reference-grid]').waitFor();
  assert.match(await auto.locator('#simulation-detail').innerText(), /1 km/);
  assert.match(await auto.locator('#coarse-limitations').innerText(), /not fine-scale/);
  await auto.locator('#status').waitFor({ state: 'hidden' });
  assert.equal(await auto.locator('#status').innerText(), '');
  await auto.screenshot({ path: `${output}/online-grid.png` });
  await auto.locator('#reset').click();
  await auto.locator('#status').waitFor({ state: 'hidden' });
  await auto.locator('#place').click();
  const gridClicked = auto.waitForResponse(r => r.url().endsWith('/api/seed'));
  const gridBox = await auto.locator('#map').boundingBox();
  await auto.mouse.click(gridBox.x + gridBox.width / 2, gridBox.y + gridBox.height / 2);
  assert.equal((await gridClicked).status(), 200);
  await auto.locator('#place').click();
  const installedExample = config.local_spread.regions[0].example_ignition;
  await auto.locator('#latitude').fill(String(installedExample.latitude));
  await auto.locator('#longitude').fill(String(installedExample.longitude));
  await auto.locator('#coordinate-place').click();
  await auto.locator('#status.error').filter({ hasText: 'Reset before starting here' }).waitFor();
  assert.equal(await auto.locator('#map').getAttribute('data-simulation'), 'reference-grid');
  await auto.locator('#latitude').fill('46.8');
  await auto.locator('#longitude').fill('-100.8');
  const coarseStepped = auto.waitForResponse(r => r.url().endsWith('/api/step'));
  await auto.locator('#step').click();
  const stepResponse = await coarseStepped, coarseStep = await stepResponse.json();
  assert.equal(stepResponse.status(), 200, JSON.stringify(coarseStep));
  assert.equal(coarseStep.elapsed_hours, 12);
  await auto.locator('#elapsed').filter({ hasText: '+12 hours' }).waitFor();
  await auto.locator('#status').waitFor({ state: 'hidden' });
  assert.ok(coarseStep.points.every(p => p.geometry.type === 'Polygon'));
  // Losing connectivity must not erase the acknowledged grid result.
  const initialRequests = tileRequests;
  const retainedSources = await auto.locator('.leaflet-tile-pane img.leaflet-tile-loaded').evaluateAll(images => images.map(image => image.src));
  assert.ok(retainedSources.length);
  await auto.locator('#play').click();
  await auto.evaluate(() => { Object.defineProperty(navigator, 'onLine', { get: () => false, configurable: true }); window.dispatchEvent(new Event('offline')); });
  await auto.locator('#map[data-connection=offline][data-overview=ready]').waitFor();
  await auto.locator('#connection-notice').filter({ hasText: 'playback paused' }).waitFor();
  assert.match(await auto.locator('#play').innerText(), /Play/);
  assert.equal(await auto.locator('#elapsed').innerText(), '+12 hours');
  const retained = await auto.locator('.leaflet-retainedMaps-pane img').evaluateAll(images => images.map(image => ({ src: image.src, loaded: image.complete && image.naturalWidth > 0 })));
  assert.ok(retained.length && retained.every(image => image.loaded && retainedSources.includes(image.src)), 'Visible tiles must remain decoded and geographically anchored offline');
  await auto.clock.fastForward(10000);
  assert.equal(await auto.locator('#elapsed').innerText(), '+12 hours', 'Disconnect pauses automatic progression');
  assert.equal(tileRequests, initialRequests, 'Retaining the view must not request more online tiles');
  await auto.screenshot({ path: `${output}/disconnected-retained-view.png` });
  // Continuing an existing local computation is an explicit user choice.
  const offlineStep = auto.waitForResponse(r => r.url().endsWith('/api/step'));
  await auto.locator('#step').click();
  assert.equal((await offlineStep).status(), 200);
  await auto.locator('#elapsed').filter({ hasText: '+24 hours' }).waitFor();
  await auto.locator('#reset').click();
  await auto.locator('#coordinate-place').click();
  await auto.locator('#status.error').filter({ hasText: 'not installed here' }).waitFor();
  tilesWork = false;
  await auto.evaluate(() => { Object.defineProperty(navigator, 'onLine', { get: () => true, configurable: true }); window.dispatchEvent(new Event('online')); });
  // Previously decoded tiles may still be usable. A different view needs new
  // tiles and exercises provider failure rather than browser memory caching.
  await auto.locator(`#installed-regions button[data-region="${config.local_spread.regions[0].id}"]`).click();
  await auto.locator('#map[data-connection=unavailable]').waitFor();
  assert.ok(tileRequests > initialRequests);
  assert.equal(await auto.locator('.installed-boundary').count(), 2);
  tilesWork = true;
  await auto.clock.fastForward(31000);
  await auto.locator('#map[data-connection=online]').waitFor();
  await auto.locator('#latitude').fill('55.3');
  await auto.locator('#longitude').fill('-105');
  const missingTerrainSeed = auto.waitForResponse(r => r.url().endsWith('/api/seed'));
  await auto.locator('#coordinate-place').click();
  const missingResponse = await missingTerrainSeed;
  assert.equal(missingResponse.status(), 200, await missingResponse.text());
  await auto.locator('#coarse-limitations').filter({ hasText: 'Terrain missing' }).waitFor();
  await auto.locator('#status').waitFor({ state: 'hidden' });
  assert.equal(await auto.locator('#status').innerText(), '');
  await auto.screenshot({ path: `${output}/online-missing-terrain.png` });
  // Native Windows reachability must work while Chromium still says online.
  const nativePage = await online.newPage();
  nativePage.on('pageerror', e => errors.push(e.message));
  let nativeOnline = true;
  await nativePage.route('**/api/config', route => route.fulfill({ json: { ...config, desktop: { online_enabled: true } } }));
  await nativePage.route('**/api/desktop**', route => route.fulfill({ json: { online_enabled: nativeOnline, firms_configured: false } }));
  await nativePage.goto(base);
  const nativeExplorer = nativePage.frameLocator('#explorer');
  await nativeExplorer.locator('#map[data-connection=online]').waitFor();
  await nativeExplorer.locator('.coordinates summary').click();
  await nativeExplorer.locator('#latitude').fill('54');
  await nativeExplorer.locator('#longitude').fill('-124');
  const bcSeed = nativePage.waitForResponse(r => r.url().endsWith('/api/seed'));
  await nativeExplorer.locator('#coordinate-place').click();
  assert.equal((await bcSeed).status(), 200);
  await nativeExplorer.locator('#active-count').filter({ hasText: '1' }).waitFor();
  await nativeExplorer.locator('#play').click();
  nativeOnline = false;
  await nativePage.evaluate(() => window.dispatchEvent(new Event('wildfire:native-network')));
  await nativePage.locator('#network-status').filter({ hasText: 'Offline' }).waitFor();
  await nativeExplorer.locator('#connection-notice').filter({ hasText: 'playback paused' }).waitFor();
  assert.equal(await nativePage.evaluate(() => navigator.onLine), true);
  assert.equal(await nativeExplorer.locator('#elapsed').innerText(), '+0 hours');
  assert.match(await nativeExplorer.locator('#play').innerText(), /Play/);
  await nativeExplorer.locator('#coordinate-place').click();
  await nativeExplorer.locator('#status.error').filter({ hasText: 'not installed here' }).waitFor();
  nativeOnline = true;
  await nativePage.evaluate(() => window.dispatchEvent(new Event('wildfire:native-network')));
  await nativeExplorer.locator('#map[data-connection=online]').waitFor();
  await nativeExplorer.locator('#connection-notice').filter({ hasText: 'Connection restored' }).waitFor();
  assert.equal(await nativeExplorer.locator('#elapsed').innerText(), '+0 hours');
  assert.match(await nativeExplorer.locator('#play').innerText(), /Play/, 'Reconnection must not resume playback automatically');
  await online.close();
  assert.deepEqual(errors, []);
  const report = { automatic_pack_selection: regionReports, overview: true, offline_requests: external.length,
    outside_pack_rejected_offline: true, online_grid_routing: true, grid_map_click: true,
    mixed_engines_rejected: true, missing_terrain_visible: true, routine_map_notices_removed: true, actionable_errors_visible: true, grid_polygons: true, automatic_reconnection: true,
    saved_workspace_removed: true, polygon_inspection: true, disconnect_pauses_playback: true, retained_offline_tiles: true, explicit_offline_continuation: true, native_disconnect_with_stale_browser: true, layouts, errors };
  await writeFile(`${output}/report.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} finally { await browser.close(); }
