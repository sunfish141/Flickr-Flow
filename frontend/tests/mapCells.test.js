import test from 'node:test';
import assert from 'node:assert/strict';
import { selectionKey, polygonCellVisible } from '../src/mapCells.js';

test('polygon selection stays on the same cell when it burns out', () => {
  const cell = { cell_id: 'naea-1km:x=-1:y=2', status: 'active' };
  assert.equal(selectionKey(cell, true), selectionKey({ ...cell, status: 'burned' }, true));
  assert.notEqual(selectionKey(cell, true), selectionKey({ ...cell, status: 'historical' }, true));
  assert.equal(selectionKey(cell), `active:${cell.cell_id}`);
});

test('mixed polygon cells remain inspectable when either fire layer is visible', () => {
  const mixed = { status: 'active', active_area_m2: 10, burned_area_m2: 90 };
  assert.equal(polygonCellVisible(mixed, { active: false, burned: true }), true);
  assert.equal(polygonCellVisible(mixed, { active: true, burned: false }), true);
  assert.equal(polygonCellVisible(mixed, { active: false, burned: false }), false);
  assert.equal(polygonCellVisible({ ...mixed, active_area_m2: 0 }, { active: true, burned: false }), false);
});
