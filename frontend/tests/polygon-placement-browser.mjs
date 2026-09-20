// Self-contained Leaflet regression: node tests/polygon-placement-browser.mjs
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium } from 'playwright-core';

const bundle = await build({
  absWorkingDir: fileURLToPath(new URL('..', import.meta.url)),
  stdin: { contents: `
    import { useRef, useState } from 'react';
    import { createRoot } from 'react-dom/client';
    import FireMap from './src/FireMap';
    window.placements = []; window.inspections = [];
    const noop = () => {};
    const empty = { type: 'FeatureCollection', features: [] };
    function App() {
      const mapApi = useRef(null);
      const [placing, setPlacing] = useState(false);
      const [center, setCenter] = useState([53, -117]);
      const [lat, lon] = center;
      const frame = { local: true, state: {}, roads: empty, perimeters: empty, points: [{
        cell_id: 'test-cell', latitude: lat, longitude: lon, status: 'active', active_area_m2: 10000,
        cell_geometry: { type: 'Polygon', coordinates: [[[lon-.007, lat-.004],
          [lon+.007, lat-.004], [lon+.007, lat+.004], [lon-.007, lat+.004], [lon-.007, lat-.004]]] }
      }] };
      return <>
        <button id="placing" onClick={() => setPlacing(!placing)}>{placing ? 'Finish placing' : 'Place on map'}</button>
        <button id="alberta" onClick={() => { setCenter([53,-117]); mapApi.current.locate(53,-117); }}>Alberta</button>
        <button id="colorado" onClick={() => { setCenter([40,-105]); mapApi.current.locate(40,-105); }}>Colorado</button>
        <FireMap frame={frame} placing={placing} visibility={{active:true}} mapApi={mapApi}
          onPlace={(lat,lon) => window.placements.push({lat,lon})}
          onInspect={point => window.inspections.push(point.cell_id)} onView={noop}
          onConnection={noop} onError={message => { throw new Error(message); }} />
      </>;
    }
    createRoot(document.getElementById('root')).render(<App />);
  `, loader: 'jsx', resolveDir: fileURLToPath(new URL('..', import.meta.url)) },
  bundle: true, write: false, outfile: 'test.js', jsx: 'automatic',
  loader: { '.png': 'dataurl' },
});
const server = createServer((req, res) => {
  const asset = bundle.outputFiles.find(file => file.path.replaceAll('\\', '/').endsWith(req.url));
  if (asset) { res.setHeader('Content-Type', req.url.endsWith('.css') ? 'text/css' : 'text/javascript'); res.end(asset.contents); }
  else if (req.url === '/static/offline-overview.geojson') {
    res.setHeader('Content-Type', 'application/json'); res.end('{"type":"FeatureCollection","features":[]}');
  } else {
    res.setHeader('Content-Type', 'text/html');
    res.end('<!doctype html><link rel="stylesheet" href="/test.css"><style>#map{height:650px;width:1000px}</style><div id="root"></div><script type="module" src="/test.js"></script>');
  }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1100, height: 750 }, reducedMotion: 'reduce' });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  for (const region of ['alberta', 'colorado']) {
    await page.locator(`#${region}`).click();
    const cell = page.locator('.polygon-cell');
    await cell.waitFor({ state: 'visible' });
    await page.evaluate(() => { window.placements = []; window.inspections = []; });
    await page.locator('#placing').click();
    const box = await cell.boundingBox();
    for (const fraction of [.25, .75]) {
      await cell.click({ position: { x: box.width * fraction, y: box.height / 2 } });
    }
    const placed = await page.evaluate(() => ({ points: window.placements, inspected: window.inspections }));
    assert.equal(placed.points.length, 2, 'Each click inside an existing cell places exactly once');
    assert.deepEqual(placed.inspected, [], 'Placement must not open inspection or leave placement mode');
    assert.ok(placed.points[1].lon > placed.points[0].lon, 'Use the click location, not the cell center');
    const [lat, lon] = region === 'alberta' ? [53, -117] : [40, -105];
    assert.ok(placed.points.every(p => Math.abs(p.lat - lat) < .004 && Math.abs(p.lon - lon) < .007));
    await page.locator('#placing').click();
    await cell.click();
    assert.deepEqual(await page.evaluate(() => window.inspections), ['test-cell']);
    assert.equal(await page.evaluate(() => window.placements.length), 2, 'Inspection must not place another fire');
  }
  assert.deepEqual(errors, []);
  console.log('Passed: multiple placements in one cell, exact click coordinates, and inspection after placement in Alberta and Colorado.');
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
