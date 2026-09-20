function waitForRetry(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(new DOMException('Request canceled', 'AbortError')); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, milliseconds);
    if (signal?.aborted) abort();
    else signal?.addEventListener('abort', abort, { once: true });
  });
}

export async function api(path, body, signal, { onRetry } = {}) {
  const options = {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body), signal,
  };
  let response;
  for (let attempt = 0; ; attempt++) {
    response = await fetch(path, options);
    const seconds = Number(response.headers?.get('Retry-After'));
    if (response.status !== 503 || !(seconds > 0) || attempt >= 60) break;
    // Only admission-busy responses include Retry-After. Real preparation
    // failures propagate immediately. Keep the same request ticket while waiting.
    await response.body?.cancel();
    onRetry?.();
    await waitForRetry(Math.min(seconds, 5) * 1000, signal);
  }
  let data;
  try { data = await response.json(); }
  catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('The server returned an unreadable response. Try again.');
  }
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(e => e.msg).join('; ') : data.detail;
    const error = new Error(typeof detail === 'string' ? detail : 'The request failed. Try again.');
    if (response.status === 429) {
      const seconds = Number(response.headers.get('Retry-After'));
      error.retryAfterSeconds = Number.isFinite(seconds) && seconds > 0 ? Math.min(seconds, 300) : 10;
    }
    throw error;
  }
  // Static assets update immediately after a checkout, but an already running
  // Python server can still return the old point-only grid contract.
  if (!data.local && Array.isArray(data.points) && data.points.some(point => point.status !== 'historical' && !point.geometry)) {
    throw new Error('Fire geometry is missing from the server response. Restart the server after updating the app, then reload this page.');
  }
  return data;
}
