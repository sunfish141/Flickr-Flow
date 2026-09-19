import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { api } from './api';
import { emptyScenario, scenarioReducer } from './scenarioState';
import { RequestCoordinator } from './requestCoordinator';

export function useScenario() {
  const [config, setConfig] = useState(null);
  const [localRegion, setLocalRegion] = useState('');
  const expanding = localRegion === 'auto' || !!config?.local_spread?.presets?.some(region => region.id === localRegion);
  const [scenario, dispatch] = useReducer(scenarioReducer, undefined, emptyScenario);
  const [playing, setPlaying] = useState(false);
  const [busy, setBusy] = useState(null);
  const [busySeconds, setBusySeconds] = useState(0);
  const [firmsRetryAt, setFirmsRetryAt] = useState(0);
  const [firmsCooldown, setFirmsCooldown] = useState(0);
  const [speed, setSpeed] = useState(3);
  const [status, setStatus] = useState({ text: '', error: false });
  const requests = useRef(null);
  if (!requests.current) requests.current = new RequestCoordinator();
  const message = useCallback((text, error = false) => setStatus({ text, error }), []);
  const pause = useCallback(() => {
    if (requests.current.cancel()) message('Request canceled. The previous frame is retained; server preparation may still be finishing.');
    setPlaying(false);
  }, [message]);
  useEffect(() => {
    setBusySeconds(0);
    if (!busy) return;
    const started = Date.now();
    const timer = setInterval(() => setBusySeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [busy]);
  useEffect(() => {
    const tick = () => setFirmsCooldown(Math.max(0, Math.ceil((firmsRetryAt - Date.now()) / 1000)));
    tick();
    if (firmsRetryAt <= Date.now()) return;
    const timer = setInterval(() => { tick(); if (Date.now() >= firmsRetryAt) clearInterval(timer); }, 250);
    return () => clearInterval(timer);
  }, [firmsRetryAt]);
  useEffect(() => {
    const controller = new AbortController();
    api('/api/config', undefined, controller.signal).then(data => {
      setConfig(data);
      if (!data.model_ready) message(data.model_error, true);
    }).catch(error => { if (error.name !== 'AbortError') message(`Could not connect to the model server. ${error.message}`, true); });
    return () => { controller.abort(); requests.current.cancel(false); };
  }, [message]);
  useEffect(() => {
    const hide = () => {
      if (!document.hidden) return;
      // Explicit source loading survives a tab switch. Cancel only playback
      // advancement, whose automatic continuation should pause while hidden.
      setPlaying(false);
      if (requests.current.active?.kind.endsWith('step')) pause();
    };
    document.addEventListener('visibilitychange', hide);
    return () => document.removeEventListener('visibilitychange', hide);
  }, [pause]);
  const request = useCallback((kind, body, onSuccess) => requests.current.run(
    kind, signal => api(`/api/${kind}`, body, signal, {
      onRetry: () => message('Waiting for landscape preparation to finish. This request will retry automatically; you can cancel with Pause.'),
    }), {
      onBusy: setBusy,
      onSuccess,
      onError: error => {
        if (kind.includes('firms') && error.retryAfterSeconds) setFirmsRetryAt(Date.now() + error.retryAfterSeconds * 1000);
        setPlaying(false); message(error.message, true);
      },
    }), [message]);
  const frame = scenario.history[scenario.cursor];
  const advance = useCallback(async () => {
    if (requests.current.active || !frame || frame.finished) return;
    if (scenario.cursor < scenario.history.length - 1) {
      if (scenario.history[scenario.cursor + 1].finished) setPlaying(false);
      dispatch({ type: 'replay', cursor: scenario.cursor }); return;
    }
    if (frame.expanding) message('Advancing the fire and preparing any additional landscape tiles…');
    return request(frame.expanding ? 'landscape/step' : frame.local ? 'local/step' : 'step', { state: frame.state, origin_at: frame.origin_at,
      ...(frame.historical ? { historical: { start_date: frame.historical.start_date, bounds: frame.historical.bounds } } : {}) }, result => {
      dispatch({ type: 'append', frame: result });
      if (result.finished) setPlaying(false);
      if (result.local) {
        message(result.boundary_reached ? 'Fire reached the boundary of collected evidence. Start a new scenario to continue elsewhere.' : `Local scenario: ${(result.active_area_m2 / 10000).toFixed(1)} active hectares; ${(result.burned_area_m2 / 10000).toFixed(1)} burned hectares. ${result.finished ? 'End of the historical date range.' : result.extinct ? 'No active fire remains. Burned fuel stays exhausted as the clock continues.' : 'Rates and wind are scenario assumptions.'}${result.historical ? ' Historical observations use the current retained landscape.' : ''}`);
        return;
      }
      message(result.historical ? `Historical FIRMS: ${result.historical.date} (UTC), ${result.historical.detection_count} detections. ${result.finished ? 'End of the historical date range.' : 'Purple observations compared with the continuing simulation.'}` : result.extinct ? 'No active fire remains. Burned cells stay masked as the clock continues.' :
        result.terrain_missing_count ? `${result.terrain_missing_count} candidate cells have missing terrain inputs.` : '');
    });
  }, [frame, scenario.cursor, scenario.history, request, message]);
  useEffect(() => {
    if (!playing || busy) return;
    const timer = setTimeout(advance, speed * 1000);
    return () => clearTimeout(timer);
  }, [playing, busy, speed, advance]);
  const addIgnition = async (latitude, longitude, intensity) => {
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
      message('Enter a valid latitude and longitude.', true); return false;
    }
    if (scenario.ignitions.length >= 500) { message('At most 500 starting points are supported.', true); return false; }
    const ignitions = [...scenario.ignitions, { latitude, longitude, intensity }];
    if (expanding) message('Preparing local fuel and road tiles around the fire. First use may take several minutes…');
    return request(expanding ? 'landscape/seed' : localRegion ? 'local/seed' : 'seed', { ignitions, ...(localRegion && !expanding ? { region: localRegion } : {}) }, result => {
      dispatch({ type: 'replace', frame: result, ignitions, source: 'placed' });
      message(result.local ? 'Starting fuel patch added. Playback uses experimental travel rates and constant scenario wind.' : 'Starting fire added. Add more cells, or press Play to predict spread.');
    });
  };
  const loadFirms = (bounds, date = null) => request((expanding ? 'landscape/' : '') + (date ? 'firms/historical' : 'firms'), date ? { date, bounds } : bounds, result => {
    dispatch({ type: 'replace', frame: result, source: 'firms' });
    if (result.expanding) {
      message(`Loaded satellite-seeded landscape scenario: ${result.metadata.mapped_starting_cells} starting cells; ${result.metadata.unsupported_observed_cells} cells without supported vegetation. Fine ignition positions are assumptions within observed 1 km cells. ${result.historical ? 'Historical observations use the current retained landscape, not a historical reconstruction.' : 'Landscape coverage expands with the fire.'}`);
      return;
    }
    const meta = result.metadata;
    message(result.historical ? `Loaded historical FIRMS for ${result.historical.date} (UTC): ${result.historical.detection_count} detections, ${result.active_count} simulated starting cells. ${result.finished ? 'End of the historical date range.' : 'Advance a day to compare observed and simulated spread.'}` : `Loaded ${meta.eligible_detection_count} observations in ${result.active_count} active cells. ${meta.water_cells_excluded || 0} cells excluded by land coverage. ${meta.recent_detections_excluded} observations newer than 3 hours excluded. Snapshot: ${new Date(meta.as_of).toLocaleString()}.`);
  });
  const reset = () => { pause(); dispatch({ type: 'reset' }); message('Scenario reset.'); };
  const seek = step => { pause(); dispatch({ type: 'seek', step }); message(''); };
  return { config, scenario, frame, playing, busy, busySeconds, firmsCooldown, speed, setSpeed, status, message,
    localRegion, expanding, setLocalRegion: value => { reset(); setLocalRegion(value); },
    pause, advance, addIgnition, loadFirms, reset, seek,
    play: () => { if (!frame || frame.finished) return; message(''); setPlaying(true); },
  };
}
