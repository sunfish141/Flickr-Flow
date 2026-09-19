import test from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../src/api.js';

test('API sends state as JSON, passes cancellation, and loads config with GET', async t => {
  const signal = new AbortController().signal;
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (...args) => { calls.push(args); return Response.json({ ok: true }); });
  assert.deepEqual(await api('/api/step', { state: { step_index: 2 } }, signal), { ok: true });
  assert.equal(calls[0][1].signal, signal);
  assert.equal(calls[0][1].method, 'POST');
  assert.equal(calls[0][1].body, '{"state":{"step_index":2}}');
  await api('/api/config');
  assert.equal(calls[1][1].method, 'GET');
  assert.equal(calls[1][1].body, undefined);
});

test('validation details and bounded provider cooldown reach the UI', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: [{ msg: 'invalid bounds' }] }, { status: 422 }));
  await assert.rejects(api('/api/firms', {}), /invalid bounds/);
  globalThis.fetch = async () => Response.json({ detail: 'Retry later' }, { status: 429, headers: { 'Retry-After': '600' } });
  await assert.rejects(api('/api/firms', {}), error => error.message === 'Retry later' && error.retryAfterSeconds === 300);
  globalThis.fetch = async () => Response.json({}, { status: 429 });
  await assert.rejects(api('/api/firms', {}), error => error.retryAfterSeconds === 10);
});

test('unreadable responses are explained but interrupted response bodies stay canceled', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('<html>server error</html>'));
  await assert.rejects(api('/api/step', {}), /unreadable response/);
  globalThis.fetch = async () => ({ json: async () => { throw new DOMException('canceled', 'AbortError'); } });
  await assert.rejects(api('/api/step', {}), { name: 'AbortError' });
});

test('busy preparation retries the same request and does not retry permanent failures', async t => {
  const calls = [], waiting = [];
  t.mock.method(globalThis, 'fetch', async (...args) => {
    calls.push(args);
    return calls.length === 1 ? Response.json({ detail: 'busy' }, { status: 503, headers: { 'Retry-After': '.001' } }) : Response.json({ ready: true });
  });
  assert.deepEqual(await api('/api/landscape/firms', { west: -118 }, undefined, { onRetry: () => waiting.push(true) }), { ready: true });
  assert.equal(calls.length, 2);
  assert.equal(calls[0][1].body, calls[1][1].body);
  assert.equal(waiting.length, 1);
  globalThis.fetch = async () => Response.json({ detail: 'archive unavailable' }, { status: 503 });
  await assert.rejects(api('/api/landscape/firms', {}), /archive unavailable/);
});

test('canceling while waiting for preparation stops retries immediately', async t => {
  const controller = new AbortController();
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => {
    calls++;
    return Response.json({ detail: 'busy' }, { status: 503, headers: { 'Retry-After': '2' } });
  });
  await assert.rejects(api('/api/landscape/seed', {}, controller.signal, { onRetry: () => controller.abort() }), { name: 'AbortError' });
  assert.equal(calls, 1);
});

test('busy retries have a finite limit', async t => {
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => {
    calls++;
    return Response.json({ detail: 'still busy' }, { status: 503, headers: { 'Retry-After': '.001' } });
  });
  await assert.rejects(api('/api/landscape/seed', {}), /still busy/);
  assert.equal(calls, 61);
});
