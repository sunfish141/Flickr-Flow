const explorer = document.querySelector('#explorer'), error = document.querySelector('#shell-error');
let status, mapState = 'checking';
function showError(message) { error.hidden = !message; error.textContent = message || ''; }
async function api(path, body) {
  const response = await fetch(`/api/desktop${path}`, { method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Desktop request failed.');
  return data;
}
function broadcast() {
  if (!status) return;
  explorer.contentWindow.postMessage({ type: 'wildfire:network', status }, location.origin);
}
function update(data) {
  status = data;
  document.querySelector('#network-status').textContent = !data.online_enabled
    ? 'Offline · installed regions available' : mapState === 'online' ? 'Online maps · automatic'
    : mapState === 'unavailable' ? 'Offline map · reconnecting automatically' : 'Checking online maps…';
  document.querySelector('#key-status').textContent = data.firms_configured ? 'FIRMS key configured (not yet validated).' : 'No FIRMS key configured. Local simulations still work.';
  broadcast();
}
async function linkChanged() {
  try { update(await api('/connectivity', { enabled: navigator.onLine })); showError(''); }
  catch (e) { showError(e.message); }
}
window.addEventListener('message', event => {
  if (event.origin !== location.origin || event.source !== explorer.contentWindow || event.data?.type !== 'wildfire:map-connection') return;
  mapState = event.data.state;
  if (status) update(status);
});
explorer.addEventListener('load', broadcast);
window.addEventListener('online', linkChanged);
window.addEventListener('offline', linkChanged);
window.addEventListener('wildfire:native-network', () => api('').then(update).catch(e => showError(e.message)));
const settings = document.querySelector('#settings');
document.querySelector('#settings-open').addEventListener('click', () => settings.showModal());
document.querySelector('#settings-close').addEventListener('click', () => settings.close());
document.querySelector('#key-form').addEventListener('submit', async event => {
  event.preventDefault();
  const input = document.querySelector('#firms-key'), key = input.value;
  input.value = '';
  try { update(await api('/firms-key', { key })); showError(''); }
  catch (e) { showError(e.message); }
});
linkChanged();
// Keep separately opened views in sync with the process-wide online setting.
setInterval(() => api('').then(update).catch(e => showError(e.message)), 1000);
