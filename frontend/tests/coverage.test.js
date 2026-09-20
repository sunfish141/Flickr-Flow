import test from 'node:test';
import assert from 'node:assert/strict';
import { contains, installedRegionAt, placementRoute, regionForView } from '../src/coverage.js';

const geometry = { type: 'Polygon', coordinates: [[[0, 1], [1, 0], [2, 1], [1, 2], [0, 1]]] };
test('coverage uses exact polygon, not its rectangular envelope', () => {
  assert.equal(contains(geometry, 1, 1), true);
  assert.equal(contains(geometry, 0, 0), false);
  assert.equal(contains(geometry, 0, 1), true);
  assert.equal(contains(geometry, NaN, 1), false);
  assert.equal(installedRegionAt([{ id: 'a', coverage: { geometry } }], 1, 1).id, 'a');
  assert.equal(installedRegionAt([], 1, 1), undefined);
});
test('holes and multipolygons do not invent coverage', () => {
  const hole = [[.8, .8], [1.2, .8], [1.2, 1.2], [.8, 1.2], [.8, .8]];
  assert.equal(contains({ type: 'Polygon', coordinates: [...geometry.coordinates, hole] }, 1, 1), false);
  assert.equal(contains({ type: 'MultiPolygon', coordinates: [geometry.coordinates] }, 1, 1), true);
});

test('connected placements outside installed packs restore the labelled grid route', () => {
  assert.equal(placementRoute({ online: true, modelReady: true }).endpoint, 'seed');
  assert.match(placementRoute({ online: false, modelReady: true }).error, /offline/);
  assert.match(placementRoute({ online: true, modelReady: false }).error, /unavailable/);
  for (const online of [true, false]) {
    assert.equal(placementRoute({ region: { id: 'a' }, online, modelReady: false }).endpoint, 'map/seed');
  }
});

test('adding an ignition never mixes engines or installed packs', () => {
  const local = { local: true, state: { region: 'a' } }, grid = { state: {} };
  const base = { online: true, modelReady: true };
  assert.match(placementRoute({ ...base, frame: local }).error, /Reset/);
  assert.match(placementRoute({ ...base, frame: grid, region: { id: 'a' } }).error, /Reset/);
  assert.match(placementRoute({ ...base, frame: local, region: { id: 'b' } }).error, /Reset/);
  assert.equal(placementRoute({ ...base, frame: local, region: { id: 'a' } }).endpoint, 'map/seed');
  assert.equal(placementRoute({ ...base, frame: grid }).endpoint, 'seed');
});

test('satellite views use packs only for close-up views centred inside them', () => {
  const regions = [{ id: 'a', coverage: { geometry }, bounds: [0, 0, 2, 2] }];
  assert.equal(regionForView(regions, { west: -.1, east: 2.1, south: -.1, north: 2.1 }).id, 'a');
  assert.equal(regionForView(regions, { west: -10, east: 12, south: -10, north: 12 }), null);
  assert.equal(regionForView(regions, { west: 3, east: 4, south: 3, north: 4 }), null);
});

test('full-region coverage routes offline to bounded landscape tiles without mixing regions', () => {
  const region = { id: 'alberta', tiled: true, coverage: { geometry }, bounds: [0,0,2,2] };
  const frame = { local: true, expanding: true, region_id: 'alberta', state: {} };
  assert.equal(placementRoute({ region, online: false, modelReady: false }).endpoint, 'landscape/seed');
  assert.equal(placementRoute({ region, frame, online: false }).endpoint, 'landscape/seed');
  assert.match(placementRoute({ region: { ...region, id: 'colorado' }, frame, online: true }).error, /Reset/);
  assert.equal(regionForView([region], { west: 0, south: 0, east: 2, north: 2 }), null);
  assert.equal(regionForView([region], { west: .9, south: .9, east: 1.1, north: 1.1 }).id, 'alberta');
});
