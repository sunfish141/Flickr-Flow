import { useCallback, useEffect, useRef, useState } from 'react';
import PlanningMap from './PlanningMap.jsx';
import { areas, comparisonIssue, mutation, planningApi as api, planningUrl } from './planningApi.js';

const fmt = n => n.toFixed(3);
const sourceDates = pack => pack.sources.map(s => `${s.product}: ${s.component_year || s.version || s.observation_end || 'date unknown'}`).join(' · ');
function SourceNote({ pack }) {
  return <p className="source-dates">Historical, potentially stale: {sourceDates(pack)}. Road release dates are not survey dates.<br />
    Urban/unknown: {pack.coverage.urban_and_unknown_area_km2.toFixed(2)} km² unsupported. Road width unknown for {pack.coverage.unknown_width_road_pieces}/{pack.coverage.road_piece_count} pieces; grade unknown for {pack.coverage.unknown_grade_road_pieces}/{pack.coverage.road_piece_count}.</p>;
}

export default function OfflineApp() {
  const embedded = location.pathname.startsWith('/planning/');
  const [config, setConfig] = useState(null), [cases, setCases] = useState([]);
  const [current, setCurrent] = useState(null), [draft, setDraft] = useState(null);
  const [other, setOther] = useState(null), [otherId, setOtherId] = useState('');
  const [frames, setFrames] = useState([]), [otherFrames, setOtherFrames] = useState([]);
  const [hour, setHour] = useState(0), [playing, setPlaying] = useState(false);
  const [error, setError] = useState(''), [saveState, setSaveState] = useState('Saved');
  const [busy, setBusy] = useState(false), [placing, setPlacing] = useState(false);
  const [packId, setPackId] = useState(''), [coordinates, setCoordinates] = useState({ latitude: '', longitude: '' });
  const active = useRef(null), dirty = useRef(false), saving = useRef(false), editVersion = useRef(0), ticket = useRef(0);
  const followCheckpoint = useRef(false);
  const playbackIntent = useRef(null), playbackDirty = useRef(false);
  const file = useRef();
  useEffect(() => {
    const workspaceChanged = event => {
      if (event.origin === location.origin && event.source === window.parent && event.data?.type === 'wildfire:workspace' && !event.data.visible) setPlaying(false);
    };
    window.addEventListener('message', workspaceChanged);
    return () => window.removeEventListener('message', workspaceChanged);
  }, []);

  const refreshList = useCallback(async () => {
    const data = await api('scenarios');
    setCases(data.scenarios);
    if (data.save_error) setError(data.save_error);
  }, []);
  const adopt = useCallback(scenario => {
    active.current = scenario;
    setCurrent(scenario);
    setDraft({ name: scenario.name, definition: structuredClone(scenario.definition) });
    dirty.current = false;
    setSaveState('Saved');
  }, []);

  const open = useCallback(async id => {
    if (dirty.current || saving.current || playbackDirty.current) { setError('Wait for the save, or reload the last saved case after a conflict.'); return; }
    const generation = ++ticket.current;
    followCheckpoint.current = false;
    setPlaying(false); setPlacing(false);
    const [scenario, result] = await Promise.all([api(`scenarios/${id}`), api(`scenarios/${id}/frames`)]);
    if (ticket.current !== generation) return;
    adopt(scenario); setFrames(result.frames); setHour(scenario.playback_hour);
    localStorage.setItem('planning-last-case', id); // only a navigation hint; never authoritative data
  }, [adopt]);

  useEffect(() => {
    let canceled = false;
    (async () => {
      const data = await api('config');
      if (canceled) return;
      setConfig(data); setPackId(data.packs[0]?.id || '');
      const list = await api('scenarios');
      if (canceled) return;
      setCases(list.scenarios);
      const last = localStorage.getItem('planning-last-case');
      const id = list.scenarios.find(s => s.id === last)?.id || list.scenarios[0]?.id;
      if (id) await open(id);
    })().catch(e => setError(e.message));
    return () => { canceled = true; };
  }, [open]);

  // Serialize mutations. Changes typed while a save is in flight remain dirty
  // and get their own revision-checked save; no optimistic "Saved" status.
  useEffect(() => {
    if (!draft || !dirty.current) return;
    const timer = setTimeout(async () => {
      if (saving.current) return;
      const scenario = active.current, version = editVersion.current;
      saving.current = true; setSaveState('Saving…');
      try {
        const saved = await api(`scenarios/${scenario.id}`, { ...mutation(scenario), ...draft }, 'PUT');
        if (active.current?.id !== saved.id) return;
        active.current = saved; setCurrent(saved);
        if (version === editVersion.current) { dirty.current = false; setSaveState('Saved'); }
        else { setSaveState('Unsaved changes'); setDraft(d => ({ ...d })); }
        await refreshList();
      } catch (e) { setSaveState('Not saved'); setError(e.message); }
      finally { saving.current = false; }
    }, 500);
    return () => clearTimeout(timer);
  }, [draft, refreshList]);

  useEffect(() => {
    const warn = event => { if (dirty.current || saving.current || playbackDirty.current) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, []);

  useEffect(() => {
    if (!current?.id) return;
    let canceled = false, pending = false;
    const id = current.id;
    const timer = setInterval(async () => {
      if (pending || saving.current || dirty.current || playbackDirty.current) return;
      pending = true;
      try {
        const [scenario, result] = await Promise.all([api(`scenarios/${id}`), api(`scenarios/${id}/frames`)]);
        if (canceled || active.current?.id !== id || saving.current || dirty.current || playbackDirty.current) return;
        // Never roll a newer input revision backward with an old poll response.
        if (scenario.revision > active.current.revision) {
          // Another tab changed this case. Never attach its newer revision to
          // our older form: doing that would bypass the server's stale-tab CAS.
          adopt(scenario); setHour(scenario.playback_hour); setPlaying(false);
          followCheckpoint.current = false;
        } else if (scenario.revision === active.current.revision) {
          active.current = scenario; setCurrent(scenario);
        }
        setFrames(result.frames);
        if (followCheckpoint.current && scenario.checkpoint >= 0) setHour(scenario.checkpoint);
        if (scenario.error) setError(scenario.error);
        await refreshList();
      } catch (e) { if (!canceled) setError(e.message); }
      finally { pending = false; }
    }, 1500);
    return () => { canceled = true; clearInterval(timer); };
  }, [current?.id, refreshList, adopt]);

  useEffect(() => {
    let canceled = false;
    if (!otherId) { setOther(null); setOtherFrames([]); return; }
    const refresh = async () => {
      try {
        const [scenario, result] = await Promise.all([api(`scenarios/${otherId}`), api(`scenarios/${otherId}/frames`)]);
        if (!canceled) { setOther(scenario); setOtherFrames(result.frames); }
      } catch (e) { if (!canceled) setError(e.message); }
    };
    refresh(); const timer = setInterval(refresh, 2000);
    return () => { canceled = true; clearInterval(timer); };
  }, [otherId]);

  const issue = other ? comparisonIssue(current, other) : null;
  const maxHour = Math.max(0, Math.min(current?.checkpoint ?? 0, other && !issue ? other.checkpoint : Infinity));
  const visibleHour = Math.min(hour, maxHour);
  playbackIntent.current = { id: current?.id, hour: visibleHour };
  playbackDirty.current = !!current && current.playback_hour !== visibleHour;
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => setHour(h => {
      if (h >= maxHour) { setPlaying(false); return h; }
      return h + 1;
    }), 900);
    return () => clearInterval(timer);
  }, [playing, maxHour]);

  // Persist playback during playback as well as after manual seeking. Reopening
  // always pauses at the last acknowledged position, never auto-starts playback.
  useEffect(() => {
    if (!current || current.playback_hour === visibleHour || dirty.current) return;
    const id = current.id;
    let timer, canceled = false;
    setSaveState('Unsaved playback');
    const save = async () => {
      if (canceled || active.current?.id !== id) return;
      // A seek during an older save must remain queued, not disappear when
      // this debounce expires while the mutation lock is held.
      if (saving.current || dirty.current) { timer = setTimeout(save, 100); return; }
      saving.current = true; setSaveState('Saving playback…');
      try {
        const saved = await api(`scenarios/${id}/playback`, { ...mutation(active.current), playback_hour: visibleHour }, 'PUT');
        if (active.current?.id !== id) return;
        active.current = saved; setCurrent(saved);
        const intent = playbackIntent.current;
        setSaveState(dirty.current ? 'Unsaved changes' :
          intent.id === id && intent.hour === saved.playback_hour ? 'Saved' : 'Unsaved playback');
      } catch (e) { setSaveState('Playback not saved'); setError(e.message); }
      finally { saving.current = false; }
    };
    timer = setTimeout(save, 350);
    return () => { canceled = true; clearTimeout(timer); };
  }, [visibleHour, playing, current?.id, current?.playback_hour, current?.revision]);

  const edit = update => {
    dirty.current = true; editVersion.current++; setSaveState('Unsaved changes');
    setDraft(previous => update(structuredClone(previous)));
  };
  const act = async action => {
    setError(''); setBusy(true);
    try { await action(); await refreshList(); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  const create = () => act(async () => {
    const pack = config.packs.find(p => p.id === packId);
    const scenario = await api('scenarios', { request_id: crypto.randomUUID(), name: `${pack.label} — baseline`, definition: {
      pack_id: pack.id, pack_digest: pack.digest, engine_version: config.engine_version,
      origin_time: new Date().toISOString(), ignitions: [], wind: { speed_m_s: 0, from_degrees: 0 }, ...config.defaults,
    }});
    await open(scenario.id);
  });
  const clone = () => act(async () => {
    const source = active.current;
    const scenario = await api(`scenarios/${source.id}/clone`, { ...mutation(source), name: `${source.name.slice(0, 108)} — variant` });
    setOtherId(source.id); await open(scenario.id);
  });
  const operation = name => act(async () => {
    const scenario = active.current;
    const result = await api(`scenarios/${scenario.id}/${name}`, mutation(scenario));
    followCheckpoint.current = name === 'run';
    active.current = result; setCurrent(result); setPlacing(false);
  });
  const editable = current && current.checkpoint < 0 && current.status !== 'running' && !current.continuation_blocked;
  const addIgnition = ignition => {
    if (!editable || draft.definition.ignitions.length >= 50) return;
    edit(d => { d.definition.ignitions.push(ignition); return d; });
  };
  const frame = frames.find(f => f.hour === visibleHour), otherFrame = otherFrames.find(f => f.hour === visibleHour);
  const a = areas(frame), b = areas(otherFrame);
  const disabled = busy || dirty.current || saving.current || playbackDirty.current;

  if (!config) return <main><h1>{embedded ? 'Opening saved scenarios…' : 'Opening offline planner…'}</h1>{error && <p role="alert">{error}</p>}</main>;
  return <div className="planner">
    <header className="topbar"><div><span className="eyebrow">WILDFIRE ATLAS / RESEARCH WORKSPACE</span><h1>{embedded ? 'Saved scenarios' : 'Offline planning'}</h1></div>
      <div className="connection"><span className="dot" /> Local only<span>Unsigned internal build · no account</span></div></header>
    <section aria-label="Research status"><div className="research-banner"><strong>Hypothetical scenarios, not operational forecasts.</strong> Uncalibrated spread · historical cover · no general ember crossing. Do not use for response allocation.</div>
    {error && <div className="error" role="alert">{error}<button onClick={() => setError('')}>Dismiss</button></div>}
    {config.pack_errors.map(e => <div className="error" key={e}>{e}</div>)}
    {config.storage.warning && <div className="error">{config.storage.warning}</div>}</section>
    <div className="workspace"><aside className="sidebar">
      <section><h2>Case library</h2><label>Prepared region<select value={packId} onChange={e => setPackId(e.target.value)}>{config.packs.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
        <button className="primary" onClick={create} disabled={!packId || disabled}>New baseline</button>
        <label>Saved case<select value={current?.id || ''} onChange={e => act(() => open(e.target.value))} disabled={disabled}><option value="" disabled>Choose a case</option>{cases.map(s => <option key={s.id} value={s.id}>{s.name} · {s.status} · {Math.max(0, s.checkpoint)}h</option>)}</select></label>
        <input ref={file} type="file" accept=".json,application/json" hidden onChange={e => act(async () => {
          const selected = e.target.files[0]; if (!selected) return;
          if (selected.size > config.max_import_bytes - 1024) throw new Error('Import exceeds the 8 MiB portable-document limit.');
          const document = JSON.parse(await selected.text());
          const imported = await api('scenarios/import', { request_id: crypto.randomUUID(), document });
          await open(imported.id); e.target.value = '';
        })} /><button onClick={() => file.current.click()} disabled={disabled}>Import saved case</button>
      </section>
      {draft && <><section><div className="section-title"><h2>Scenario assumptions</h2><span role="status" className={saveState === 'Saved' ? 'saved' : 'unsaved'}>{saveState}</span></div>
        {saveState.includes('Not saved') && <button onClick={() => { dirty.current = false; saving.current = false; act(() => open(current.id)); }}>Reload saved case (discard unsaved edits)</button>}
        <fieldset disabled={!editable || busy}><label>Case name<input maxLength="120" value={draft.name} onChange={e => edit(d => ({ ...d, name: e.target.value }))} /></label>
          <label>Hypothetical start (UTC)<input type="datetime-local" value={draft.definition.origin_time.slice(0, 16)} onChange={e => { if (e.target.value) edit(d => { d.definition.origin_time = `${e.target.value}:00Z`; return d; }); }} /></label>
          <div className="input-pair"><label>Wind speed (m/s)<input type="number" min="0" max="60" step="0.5" value={draft.definition.wind.speed_m_s} onChange={e => edit(d => { d.definition.wind.speed_m_s = Number(e.target.value); return d; })} /></label>
          <label>Wind FROM (°)<input type="number" min="0" max="359.99" step="1" value={draft.definition.wind.from_degrees} onChange={e => edit(d => { d.definition.wind.from_degrees = Number(e.target.value); return d; })} /></label></div>
          <p className="help">Meteorological direction: 0° from north, 90° from east. 0 m/s is calm.</p>
          <label>Horizon (hours)<input type="number" min="1" max="96" value={draft.definition.horizon_hours} onChange={e => edit(d => { d.definition.horizon_hours = Number(e.target.value); return d; })} /></label>
          <p className="help">100 m mesh · hourly output · no automatic resolution changes</p>
          <button className={placing ? 'primary' : ''} aria-pressed={placing} onClick={() => setPlacing(p => !p)}>{placing ? 'Placing ignitions: click left map' : 'Place ignition on map'}</button>
          <div className="input-pair"><label>Latitude<input type="number" step="any" value={coordinates.latitude} onChange={e => setCoordinates(p => ({ ...p, latitude: e.target.value }))} /></label><label>Longitude<input type="number" step="any" value={coordinates.longitude} onChange={e => setCoordinates(p => ({ ...p, longitude: e.target.value }))} /></label></div>
          <button disabled={!coordinates.latitude || !coordinates.longitude} onClick={() => addIgnition({ latitude: Number(coordinates.latitude), longitude: Number(coordinates.longitude) })}>Add coordinate ignition</button>
          <p>{draft.definition.ignitions.length} ignition(s) <button className="inline" onClick={() => edit(d => { d.definition.ignitions = []; return d; })}>Clear</button></p>
        </fieldset>
        {!editable && <p className="help">Inputs are retained with this result. Clone to change assumptions.</p>}
        {current.continuation_blocked && <p className="warning">{current.continuation_blocked}</p>}
        <div className="button-row"><button className="primary" disabled={disabled || !!current.continuation_blocked || current.status === 'running' || current.checkpoint >= current.definition.horizon_hours || !draft.definition.ignitions.length} onClick={() => operation('run')}>{current.checkpoint >= 0 ? 'Continue calculation' : 'Run scenario'}</button>
          <button disabled={busy || current.status !== 'running'} onClick={() => operation('pause')}>Pause calculation</button></div>
        <button onClick={clone} disabled={disabled}>Clone as variant</button>
        <p className="help">{current.status} · committed through {current.checkpoint < 0 ? 'no frames yet' : `hour ${current.checkpoint}`} · revision {current.revision}</p>
      </section><section><h2>Reproducibility</h2><details><summary>Policy, source dates & coverage</summary><pre>{JSON.stringify({ policy: draft.definition.policy, coverage: current.pack_snapshot.coverage, sources: current.pack_snapshot.sources, pack_digest: current.definition.pack_digest, engine: current.definition.engine_version }, null, 2)}</pre></details>
        <div className="exports">{['json', 'html', 'geojson'].map(format => <a key={format} href={planningUrl(`scenarios/${current.id}/export?format=${format}`)}>Export {format.toUpperCase()}</a>)}</div></section></>}
      <p className="help">Managed storage at startup: {(config.storage.used_bytes / 1e9).toFixed(2)} / 20 GB. No automatic deletion. Original training archives are separate.</p>
    </aside>
    <main className="canvas"><div className="comparison-toolbar"><div><h2>Ignition & wind sensitivity</h2><p>Compare the same coverage, engine and elapsed time.</p></div>
      <label>Comparison case<select value={otherId} onChange={e => { setOtherId(e.target.value); setPlaying(false); }}><option value="">Single case</option>{cases.filter(s => s.id !== current?.id).map(s => <option value={s.id} key={s.id}>{s.name}</option>)}</select></label></div>
      {issue && <p className="error">{issue}</p>}
      {!current ? <div className="empty"><h2>A local workspace for “what if?”</h2><p>Create a baseline, place an ignition, then clone it to explore a different wind. Every saved result keeps its original assumptions.</p><p>Regional packs must be verified before simulations are available.</p></div> : <>
        <div className={`maps ${other && !issue ? 'split' : ''}`}><article className="map-card"><div className="map-heading"><span>A / SELECTED CASE</span><h3>{current.name}</h3><p>Active {fmt(a.active)} km² · burned {fmt(a.burned)} km²</p></div>
          <SourceNote pack={current.pack_snapshot} />
          <PlanningMap pack={current.pack_snapshot} frame={frame} ignitions={draft.definition.ignitions} onIgnition={placing && editable ? addIgnition : null} label="Selected scenario offline map" />
          {frame?.result.boundary_reached && <p className="warning">Modeled spread reached the pack boundary. Beyond this coverage is unsupported.</p>}</article>
          {other && !issue && <article className="map-card"><div className="map-heading"><span>B / COMPARISON</span><h3>{other.name}</h3><p>Active {fmt(b.active)} km² · burned {fmt(b.burned)} km²</p></div><SourceNote pack={other.pack_snapshot} /><PlanningMap pack={other.pack_snapshot} frame={otherFrame} ignitions={other.definition.ignitions} label="Comparison scenario offline map" />{otherFrame?.result.boundary_reached && <p className="warning">Boundary reached; outside coverage unsupported.</p>}</article>}</div>
        <div className="timeline"><button onClick={() => { followCheckpoint.current = false; if (!playing && visibleHour >= maxHour) setHour(0); setPlaying(p => !p); }} disabled={!maxHour}>{playing ? 'Pause playback' : 'Play saved frames'}</button><label>Elapsed hour {visibleHour}<input aria-label="Synchronized elapsed hour" type="range" min="0" max={maxHour} value={visibleHour} onChange={e => { followCheckpoint.current = false; setPlaying(false); setHour(Number(e.target.value)); }} /></label><span>{maxHour}h saved{other ? ' in both cases' : ''}</span></div>
        {other && !issue && frame && otherFrame && <p className="difference">A − B at hour {visibleHour}: active {fmt(a.active-b.active)} km² · burned {fmt(a.burned-b.burned)} km². Sensitivity only—not protection effectiveness.</p>}
        <div className="legend"><span><i style={{ background: '#51745c' }} /> Vegetation</span><span><i style={{ background: '#96c8dd' }} /> Water</span><span><i style={{ background: '#b08baa' }} /> Urban / unknown: unsupported</span><span><i style={{ background: '#f15b26' }} /> Modeled active</span><span><i style={{ background: '#632f40' }} /> Modeled burned</span></div>
        <p className="attribution">Reference cover: CEC / NALCMS (historical sources; see dates). Roads: © OpenStreetMap contributors, Overture Maps Foundation and feature sources. Roads are not guaranteed firebreaks.</p>
      </>}
      <section className="limitations"><h2>What these scenarios cannot tell you</h2><ul>{config.limitations.map(text => <li key={text}>{text}</li>)}</ul></section>
    </main></div>
  </div>;
}
