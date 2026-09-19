export async function api(path, body, signal) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body), signal,
  });
  let data;
  try { data = await response.json(); }
  catch { throw new Error('The server returned an unreadable response. Try again.'); }
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(e => e.msg).join('; ') : data.detail;
    const error = new Error(typeof detail === 'string' ? detail : 'The request failed. Try again.');
    if (response.status === 429) {
      const seconds = Number(response.headers.get('Retry-After'));
      error.retryAfterSeconds = Number.isFinite(seconds) && seconds > 0 ? Math.min(seconds, 300) : 10;
    }
    throw error;
  }
  return data;
}
