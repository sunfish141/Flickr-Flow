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
