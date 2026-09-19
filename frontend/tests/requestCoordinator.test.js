import test from 'node:test';
import assert from 'node:assert/strict';
import { RequestCoordinator } from '../src/requestCoordinator.js';

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function observer() {
  const events = [];
  return { events, onBusy: value => events.push(['busy', value]),
    onSuccess: value => events.push(['success', value]), onError: error => events.push(['error', error.message]) };
}

test('only one operation runs; completion releases the next request', async () => {
  const requests = new RequestCoordinator(), pending = deferred(), callbacks = observer();
  const first = requests.run('step', () => pending.promise, callbacks);
  assert.equal(await requests.run('seed', () => assert.fail('overlapping request'), callbacks), false);
  pending.resolve('frame');
  assert.equal(await first, true);
  assert.equal(requests.active, null);
  assert.deepEqual(callbacks.events, [['busy', 'step'], ['success', 'frame'], ['busy', null]]);
  assert.equal(await requests.run('seed', async () => 'seed', callbacks), true);
});

for (const outcome of ['resolve', 'reject']) {
  test(`cancel ignores a late ${outcome} and its cleanup while a replacement is pending`, async () => {
    const requests = new RequestCoordinator(), old = deferred(), next = deferred(), callbacks = observer();
    let signal;
    const first = requests.run('step', received => { signal = received; return old.promise; }, callbacks);
    assert.equal(requests.cancel(), true);
    assert.equal(signal.aborted, true);
    const second = requests.run('firms/historical', () => next.promise, callbacks);
    old[outcome](outcome === 'resolve' ? 'stale frame' : new Error('stale error'));
    assert.equal(await first, false);
    assert.equal(requests.active.kind, 'firms/historical');
    assert.deepEqual(callbacks.events, [['busy', 'step'], ['busy', null], ['busy', 'firms/historical']]);
    next.resolve('new frame');
    assert.equal(await second, true);
    assert.deepEqual(callbacks.events.slice(-2), [['success', 'new frame'], ['busy', null]]);
  });
}

test('unmount cancellation suppresses every subsequent callback', async () => {
  const requests = new RequestCoordinator(), pending = deferred(), callbacks = observer();
  const task = requests.run('seed', () => pending.promise, callbacks);
  requests.cancel(false);
  pending.resolve('late seed');
  assert.equal(await task, false);
  assert.deepEqual(callbacks.events, [['busy', 'seed']]);
  assert.equal(requests.cancel(), false);
});

test('current errors release the request; abort errors do not show an error', async () => {
  const requests = new RequestCoordinator(), callbacks = observer();
  await requests.run('firms', async () => { throw new Error('provider unavailable'); }, callbacks);
  assert.deepEqual(callbacks.events, [['busy', 'firms'], ['error', 'provider unavailable'], ['busy', null]]);
  callbacks.events.length = 0;
  await requests.run('step', async () => { throw new DOMException('canceled', 'AbortError'); }, callbacks);
  assert.deepEqual(callbacks.events, [['busy', 'step'], ['busy', null]]);
});
