import test from 'node:test';
import assert from 'node:assert/strict';
import { areas, comparisonIssue, planningUrl } from '../src/planningApi.js';

test('planner API and exports use the mount prefix only in the combined app', () => {
  const previous = globalThis.location;
  try {
    globalThis.location = { pathname: '/' };
    assert.equal(planningUrl('scenarios/a/export'), '/api/scenarios/a/export');
    globalThis.location = { pathname: '/planning/' };
    assert.equal(planningUrl('scenarios/a/export'), '/planning/api/scenarios/a/export');
  } finally { globalThis.location = previous; }
});

test('comparison refuses mismatched pack, mesh, engine or time interval', () => {
  const a = { definition: { pack_digest: 'a', engine_version: 'v1', mesh_m: 100, output_interval_minutes: 60 } };
  assert.equal(comparisonIssue(a, structuredClone(a)), null);
  for (const key of Object.keys(a.definition)) {
    const b = structuredClone(a); b.definition[key] = 'different';
    assert.match(comparisonIssue(a, b), /blocked/);
  }
});
test('areas are modeled active/burned square kilometres, not probabilities', () => {
  assert.deepEqual(areas(), { active: 0, burned: 0 });
  assert.deepEqual(areas({ result: { perimeters: { features: [
    { properties: { status: 'active', area_m2: 2e6 } }, { properties: { status: 'burned', area_m2: 3e6 } },
  ] } } }), { active: 2, burned: 3 });
});
