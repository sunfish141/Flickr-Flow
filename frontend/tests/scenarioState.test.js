import test from 'node:test';
import assert from 'node:assert/strict';
import { emptyScenario, scenarioReducer as reduce, HISTORY_LIMIT } from '../src/scenarioState.js';

const frame = step => ({ state: { step_index: step, burned: ['old-cell'], fuel: { live: .4 } }, origin_at: '2026-05-11T00:00:00Z' });

test('replacing a source discards its timeline and placed ignitions', () => {
  const placed = reduce(emptyScenario(), { type: 'replace', frame: frame(0), source: 'placed', ignitions: [{ latitude: 53 }] });
  const advanced = reduce(placed, { type: 'append', frame: frame(1) });
  const replacement = frame(0);
  const firms = reduce(advanced, { type: 'replace', frame: replacement, source: 'firms' });
  assert.deepEqual(firms, { history: [replacement], cursor: 0, ignitions: [], source: 'firms' });
  assert.equal(placed.history.length, 1);
  assert.deepEqual(reduce(firms, { type: 'reset' }), emptyScenario());
});

test('rolling history retains complete fuel state and seeks absolute historical steps', () => {
  let state = emptyScenario();
  const frames = Array.from({ length: 131 }, (_, i) => frame(i * 2));
  for (const value of frames) state = reduce(state, { type: 'append', frame: value });
  assert.equal(state.history.length, HISTORY_LIMIT);
  assert.equal(state.history[0].state.step_index, 6);
  assert.equal(state.cursor, 127);
  state = reduce(state, { type: 'seek', step: 60 });
  assert.equal(state.history[state.cursor], frames[30]);
  assert.deepEqual(state.history[state.cursor].state.fuel, { live: .4 });
  assert.deepEqual(state.history[state.cursor].state.burned, ['old-cell']);
  assert.equal(reduce(state, { type: 'seek', step: 0 }), state);
});

test('replay advances retained frames once per render without changing their state', () => {
  let state = emptyScenario();
  for (let i = 0; i < 3; i++) state = reduce(state, { type: 'append', frame: frame(i) });
  state = reduce(state, { type: 'seek', step: 0 });
  const history = state.history;
  const next = reduce(state, { type: 'replay', cursor: 0 });
  assert.equal(next.cursor, 1);
  assert.equal(next.history, history);
  assert.equal(reduce(next, { type: 'replay', cursor: 0 }), next);
  const last = reduce(next, { type: 'replay', cursor: 1 });
  assert.equal(reduce(last, { type: 'replay', cursor: 2 }), last);
});
