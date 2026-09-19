export const planningUrl = path => `${globalThis.location?.pathname.startsWith('/planning/') ? '/planning' : ''}/api/${path}`;

export async function planningApi(path, body, method) {
  const response = await fetch(planningUrl(path), {
    method: method || (body === undefined ? 'GET' : 'POST'),
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return data;
}

export const mutation = scenario => ({ request_id: crypto.randomUUID(), expected_revision: scenario.revision });

export function comparisonIssue(a, b) {
  if (!a || !b) return 'Choose a second case to compare.';
  for (const key of ['pack_digest', 'engine_version', 'mesh_m', 'output_interval_minutes']) {
    if (a.definition[key] !== b.definition[key]) return `Comparison blocked: ${key.replaceAll('_', ' ')} differs.`;
  }
  return null;
}

export function areas(frame) {
  const values = { active: 0, burned: 0 };
  for (const f of frame?.result.perimeters.features || []) values[f.properties.status] += f.properties.area_m2 / 1e6;
  return values;
}
