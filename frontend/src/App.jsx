import { useEffect, useRef, useState } from 'react';
import FireMap from './FireMap';
import Inspector from './Inspector';
import HistoricalInspector from './HistoricalInspector';
import CellList from './CellList';
import { useScenario } from './useScenario';

export default function App() {
  const sim = useScenario();
  const { config, frame, scenario, playing, busy, status } = sim;
  const [mode, setMode] = useState('place');
  const [placing, setPlacing] = useState(false);
  const [intensity, setIntensity] = useState(70);
  const [coordinates, setCoordinates] = useState({ latitude: '53.02', longitude: '-117.31' });
  const [scope, setScope] = useState('all');
  const [historicalMode, setHistoricalMode] = useState(false);
  const [historicalDate, setHistoricalDate] = useState('2026-05-11');
  const [basemap, setBasemap] = useState(true);
  const [visibility, setVisibility] = useState({ active: true, burned: true, candidate: false, historical: true });
  const [selectedCell, setSelectedCell] = useState(null);
  const mapApi = useRef(null), help = useRef(null), inspectorHeading = useRef(null), returnFocus = useRef(null);
  const points = [...(frame?.points || []), ...(frame?.historical?.points || [])];
  const selected = points.find(p => `${p.status}:${p.cell_id}` === selectedCell);
  const daily = !!frame?.historical;
  const stepHours = daily ? 24 : 12;
  const historicalDates = config?.historical_firms;
  const presets = config?.local_spread?.presets || [];
  const selectedPreset = presets.find(region => region.id === sim.localRegion);
  const validHistoricalDate = historicalDate.length === 10 && historicalDate >= (historicalDates?.min_date ?? '2026-05-11') && historicalDate <= (historicalDates?.max_date ?? '2026-08-21');
  const ready = sim.localRegion ? config?.local_spread?.available : config?.model_ready;
  const canPlace = ready && !busy && !playing && (!frame || (frame.state.step_index === 0 && scenario.source === 'placed'));
  const first = scenario.history[0]?.state.step_index ?? 0, last = scenario.history.at(-1)?.state.step_index ?? 0;
  const inspect = (point, trigger) => {
    sim.pause(); setPlacing(false);
    returnFocus.current = trigger || document.getElementById('map');
    setSelectedCell(`${point.status}:${point.cell_id}`);
    if (`${point.status}:${point.cell_id}` === selectedCell) inspectorHeading.current?.focus();
  };
  useEffect(() => { if (selectedCell) inspectorHeading.current?.focus(); }, [selectedCell]);
  const closeInspector = () => {
    setSelectedCell(null);
    if (returnFocus.current?.isConnected) returnFocus.current.focus();
    else document.getElementById('cell-list')?.querySelector('summary')?.focus();
  };
  const togglePlay = () => {
    if (playing || busy) sim.pause();
    else { setPlacing(false); sim.play(); }
  };
  useEffect(() => {
    const keydown = e => {
      if (e.key === 'Escape') { sim.pause(); setPlacing(false); if (selectedCell && !help.current.open) closeInspector(); }
      // Space retains native behavior on links, summaries, controls, and the map.
      if (e.code === 'Space' && e.target === document.body && !help.current.open && frame && ready && !frame.finished) {
        e.preventDefault(); togglePlay();
      }
    };
    document.addEventListener('keydown', keydown);
    return () => document.removeEventListener('keydown', keydown);
  });
  const add = async (lat, lon) => canPlace && sim.addIgnition(lat, lon, intensity / 100);
  const fit = () => {
    const fitted = points.filter(p => p.status !== 'candidate');
    if (fitted.length) mapApi.current?.fit(fitted); else sim.message('Place a fire or load FIRMS detections first.');
  };
  // Fit new satellite scenarios once, after React commits the returned frame.
  useEffect(() => { if (scenario.source === 'firms' && frame?.state.step_index === 0) mapApi.current?.fit([...frame.points, ...(frame.historical?.points || [])]); }, [scenario.source, frame?.origin_at]);
  async function loadFirms() {
    sim.pause(); setPlacing(false); setSelectedCell(null);
    const [west, south, east, north] = config.firms_bounds;
    const view = mapApi.current.bounds();
    const bounds = scope === 'all' && !sim.expanding ? { west, south, east, north } : {
      west: Math.max(west, view.getWest()), south: Math.max(south, view.getSouth()),
      east: Math.min(east, view.getEast()), north: Math.min(north, view.getNorth()),
    };
    sim.message(sim.expanding ? 'Loading satellite observations and preparing local fuel and road tiles. First use may take several minutes…' : historicalMode ? `Loading retained FIRMS for ${historicalDate}…` : 'Loading current observations from three VIIRS satellites…');
    await sim.loadFirms(bounds, historicalMode ? historicalDate : null);
  }
  const switchMode = value => { sim.pause(); setPlacing(false); if (value === 'firms' && sim.localRegion && !sim.expanding) sim.setLocalRegion(config?.local_spread?.expanding ? 'auto' : ''); setMode(value); };
  return <>
    <a href="#scenario-controls" className="skip-link">Skip to scenario controls</a>
    <a href="#cell-list" className="skip-link">Skip to cell list</a>
    <header className="topbar">
      <a className="brand" href="/" aria-label="Wildfire Atlas home"><span className="brand-icon" aria-hidden="true">◈</span><span>Wildfire <strong>Atlas</strong><small>SPREAD EXPLORER</small></span></a>
      <div className="header-meta">RESEARCH PREVIEW · NORTH AMERICA</div>
      <button id="help" className="quiet-button" onClick={() => { sim.pause(); help.current.showModal(); }}>Help, data & privacy</button>
    </header>
    <main className="workspace">
      <aside id="scenario-controls" tabIndex={-1} className="sidebar" aria-label="Scenario controls">
        <div className="section-heading"><span className="eyebrow">YOUR WORKSPACE</span></div>
        <h1>A spark.<br /> A possible future.</h1>
        <p className="intro">Explore how a fire could spread, one twelve-hour step at a time.</p>
        <p className="research-note">Research simulation. No weather inputs. Do not use for evacuation or emergency decisions; follow local authorities.</p>
        <div className="tabs" role="group" aria-label="Starting state source">
          <button id="place-tab" className={`tab ${mode === 'place' ? 'selected' : ''}`} aria-pressed={mode === 'place'} aria-controls="place-panel" onClick={() => switchMode('place')}>Place a fire</button>
          <button id="firms-tab" className={`tab ${mode === 'firms' ? 'selected' : ''}`} aria-pressed={mode === 'firms'} aria-controls="firms-panel" onClick={() => switchMode('firms')}>FIRMS detections</button>
        </div>
          {config?.local_spread?.available && <div className="local-scenario-controls">
            <label htmlFor="local-region">Simulation<select id="local-region" value={sim.localRegion} disabled={!!busy || playing} onChange={e => {
              const value = e.target.value;
              sim.setLocalRegion(value); setPlacing(false); setSelectedCell(null);
              const region = [...presets, ...config.local_spread.regions].find(r => r.id === value);
              if (region) mapApi.current?.fitRegion(region.bounds);
              if (region?.example_ignition) setCoordinates({ latitude: String(region.example_ignition.latitude), longitude: String(region.example_ignition.longitude) });
            }}><option value="">1 km spread model</option>{config.local_spread.expanding && <option value="auto">Polygon spread · roads & fuel</option>}{[...presets, ...(mode === 'place' && !presets.length ? config.local_spread.regions : [])].map(r => <option key={r.id} value={r.id}>{r.label} · polygon spread</option>)}</select></label>
            {selectedPreset && <p className="hint">{selectedPreset.label} view. Zoom in to place a fire, or use the example coordinates below. Fuel and road coverage loads around each fire as it spreads.</p>}
            {sim.localRegion && <p className="hint">Experimental {config.local_spread.mesh_m} m fuel patches with road barriers. Uncalibrated travel rates and constant scenario wind; urban mixtures and structures are unsupported. Unknown road widths use a {config.local_spread.policy.unknown_road_width_m} m assumption where surface type is mapped. {sim.expanding ? 'Roads are read from a local archive. Landscape tiles are built as the fire spreads; first preparation may take several minutes.' : 'Place within the selected region.'}</p>}
          </div>}
        {config?.local_spread?.expanding_error && <p className="hint">Expanding fuel and road simulation is unavailable on this server.</p>}
        {Object.keys(config?.data_preparation?.errors || {}).length > 0 && <p className="hint">Some vegetation or polygon data could not be prepared. Check the server startup logs, then restart to retry.</p>}
        <section id="place-panel" hidden={mode !== 'place'} aria-label="Place starting fires">
          <div className="label-row"><label htmlFor="intensity">Starting intensity</label><output id="intensity-value" htmlFor="intensity">{intensity}%</output></div>
          <input id="intensity" type="range" min="0" max="100" step="5" value={intensity} disabled={!!sim.localRegion} aria-valuetext={`${intensity} percent`} onChange={e => setIntensity(Number(e.target.value))} />
          <div className="range-labels"><span>Low</span><span>High</span></div>
          <button id="place" className={`primary-button ${placing ? 'placing' : ''}`} aria-pressed={placing} disabled={!canPlace} onClick={() => { sim.pause(); setPlacing(!placing); }}>{placing ? '× Finish placing' : '+ Place on map'}</button>
          <p className="hint" id="placement-hint">{frame && (frame.state.step_index > 0 || scenario.source !== 'placed') ? 'Reset the scenario to place new fires.' : 'Click the map or enter coordinates below to add a starting fire.'}</p>
          <details className="coordinates"><summary>Place by coordinates</summary>
            <form onSubmit={async e => {
              e.preventDefault(); const values = new FormData(e.currentTarget);
              const lat = Number(values.get('latitude')), lon = Number(values.get('longitude'));
              if (await add(lat, lon)) mapApi.current?.locate(lat, lon);
            }}><div className="coordinate-fields">
              <label htmlFor="latitude">Latitude<input id="latitude" name="latitude" type="number" min="24" max="84" step="any" required value={coordinates.latitude} onChange={e => setCoordinates(value => ({ ...value, latitude: e.target.value }))} /></label>
              <label htmlFor="longitude">Longitude<input id="longitude" name="longitude" type="number" min="-179" max="-50" step="any" required value={coordinates.longitude} onChange={e => setCoordinates(value => ({ ...value, longitude: e.target.value }))} /></label>
            </div><button id="coordinate-place" className="secondary-button" disabled={!canPlace}>Add fire here</button></form>
          </details>
        </section>
        <section id="firms-panel" hidden={mode !== 'firms'} aria-label="Load satellite observations">
          <p className="panel-copy">Start with NASA satellite detections across North America or in your map view.</p>
          <label className="firms-scope" htmlFor="firms-scope">Fetch area<select id="firms-scope" value={sim.expanding ? 'view' : scope} disabled={!!busy} onChange={e => setScope(e.target.value)}><option value="all" disabled={sim.expanding}>All North America</option><option value="view">Visible map area</option></select></label>
          {sim.expanding && <p className="hint">Zoom in around the fire before loading. Detailed scenarios support up to 500 starting cells{config?.local_spread?.limits && <> across {config.local_spread.limits.max_tiles} tiles ({config.local_spread.limits.max_area_km2.toLocaleString()} km²), subject to the fuel-patch budget</>}. To load all North America, select the 1 km spread model.</p>}
          <label className="layer-toggle" htmlFor="historical-mode"><span>Historical mode</span><input id="historical-mode" type="checkbox" checked={historicalMode} onChange={e => { sim.pause(); setHistoricalMode(e.target.checked); }} /></label>
          {historicalMode && <div className="historical-controls">
            <label htmlFor="historical-date">Observation date (UTC)</label>
            <input id="historical-date" type="date" min={historicalDates?.min_date ?? '2026-05-11'} max={historicalDates?.max_date ?? '2026-08-21'} value={historicalDate} required onChange={e => { sim.pause(); setHistoricalDate(e.target.value); }} />
            <p className="hint">May 11–August 21, 2026. Purple rings show observations; orange points show simulated fire. Advance one day to compare their spread.</p>
          </div>}
          <button id="load-firms" className="primary-button" disabled={!ready || !!busy || playing || sim.firmsCooldown > 0 || (historicalMode ? !historicalDates?.available || !validHistoricalDate : !config?.firms_configured)} onClick={loadFirms}>{busy?.includes('firms') ? 'Loading satellites…' : sim.firmsCooldown > 0 ? `Retry in ${sim.firmsCooldown} s` : historicalMode ? '↓ Load historical FIRMS' : '↓ Load current FIRMS'}</button>
          {config && !(historicalMode ? historicalDates?.available : config.firms_configured) && <p className="hint">{historicalMode ? 'The retained historical FIRMS archive is unavailable on this server.' : 'Satellite loading is unavailable on this server. You can still place a fire.'}</p>}
          <p className="hint">{historicalMode ? 'Loading replaces the scenario and fixes the comparison area. Each day uses two 12-hour predictions without reseeding from later observations. The starting simulation uses observations 3–24 hours before the end of the selected UTC day.' : 'Observations are 3–24 hours old across three VIIRS satellites, combined into 1 km cells. Loading replaces this scenario.'}</p>
        </section>
        <div className="sidebar-divider" />
        <div className="label-row"><h2>Scenario overview</h2><span id="state-badge" className="badge">{playing ? 'RUNNING' : frame ? 'PAUSED' : 'READY'}</span></div>
        <div className="stats"><div><strong id="active-count">{frame?.active_count ?? 0}</strong><span>Active cells</span></div><div><strong id="burned-count">{frame?.burned_count ?? 0}</strong><span>Burned cells</span></div></div>
        <div className="scenario-detail"><span>Simulated time</span><strong id="elapsed">+{frame?.elapsed_hours ?? 0} hours</strong></div>
        <div className="scenario-detail"><span>New ignitions</span><strong id="new-count">{frame?.new_ignition_count ?? '—'}</strong></div>
        {daily && <div className="historical-summary" role="status"><strong>Historical comparison · {frame.historical.date} UTC</strong><p>{frame.historical.detection_count} observed detections in {frame.historical.cell_count} cells. {frame.finished ? 'End of available dates.' : 'Daily observations advance with predictions.'}</p></div>}
        <div className="scenario-detail"><span>Simulation detail</span><strong>{sim.localRegion ? `${config?.local_spread?.mesh_m} m patches` : '1 km² / cell'}</strong></div>
        {frame?.local && <p className="hint">{(frame.active_area_m2 / 10000).toFixed(1)} active ha · {(frame.burned_area_m2 / 10000).toFixed(1)} burned ha. Counts summarize 1 km cells; a cell may contain both active and burned patches.</p>}
        <div className="sidebar-divider" /><h2>Map layers</h2>
        {Object.entries({ active: 'Active fire', burned: 'Burned cells', candidate: 'Spread candidates' }).map(([key, label]) =>
          <label className="layer-toggle" key={key}><span><i className={`legend-dot ${key}`} aria-hidden="true" />{label}</span><input id={`show-${key === 'candidate' ? 'candidates' : key}`} type="checkbox" checked={visibility[key]} onChange={e => setVisibility(value => ({ ...value, [key]: e.target.checked }))} /></label>)}
        {daily && <label className="layer-toggle"><span><i className="legend-dot historical" aria-hidden="true" />Historical FIRMS (purple)</span><input id="show-historical" type="checkbox" checked={visibility.historical} onChange={e => setVisibility(value => ({ ...value, historical: e.target.checked }))} /></label>}
        <label className="layer-toggle"><span>OpenStreetMap basemap</span><input id="show-basemap" type="checkbox" checked={basemap} onChange={e => setBasemap(e.target.checked)} /></label>
        <p className="hint">The basemap sends your IP address, site origin, and viewed map tiles to OpenStreetMap. Turn it off to stop loading tiles. <a href="https://osmfoundation.org/wiki/Privacy_Policy">Provider privacy policy</a>.</p>
        <CellList points={points} onInspect={inspect} />
        <div className="sidebar-footer"><div><strong id="model-name">{config?.model_name ?? 'Loading model…'}</strong><p>Experimental spread · <a href="/static/THIRD_PARTY_LICENSES.txt">Software notices</a></p></div></div>
      </aside>
      <section className="map-workspace" aria-label="Map and simulation playback">
        <FireMap frame={frame} selectedCell={selectedCell} visibility={visibility} placing={placing && canPlace} basemap={basemap} mapApi={mapApi} onPlace={add} onInspect={inspect} onGroup={() => { sim.pause(); setPlacing(false); }} onError={text => sim.message(text, true)} />
        <div className="map-title">{selectedPreset ? `${selectedPreset.label.toUpperCase()} · POLYGON SPREAD` : sim.localRegion ? 'LOCAL LANDSCAPE · EXPERIMENTAL SCENARIO' : 'NORTH AMERICA · 1 KM GRID'}</div>
        <button id="fit" className="map-button" onClick={fit}>Fit fires</button>
        <div id="map-instruction" className="map-instruction">{placing ? 'Click the map to add starting fire cells.' : frame ? 'Inspect cells on the map or in the scenario cell list.' : 'Start with a fire or current satellite detections.'}</div>
        <div id="status" className={`status ${status.error ? 'error' : ''} ${status.text ? '' : 'empty'}`} role="status" aria-live="polite" aria-atomic="true">{status.text}{busy && <span aria-hidden="true"> · {sim.busySeconds} s elapsed</span>}</div>
        {selected && (selected.status === 'historical' ? <HistoricalInspector point={selected} historical={frame.historical} onClose={closeInspector} headingRef={inspectorHeading} /> : <Inspector point={selected} frame={frame} onClose={closeInspector} headingRef={inspectorHeading} />)}
        <section className="playback" aria-label="Simulation playback">
          <div className="playback-top"><div className="label-row timeline-title"><span id="timeline-heading" className="eyebrow">{first ? `RECENT TIMELINE · ${scenario.history.length} STEPS` : 'SIMULATION TIMELINE'}</span><strong id="valid-time" role="status">{frame ? `${new Date(frame.valid_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })} UTC · +${frame.elapsed_hours} h` : 'Place a fire to begin'}</strong></div><button id="reset" className="reset-button" disabled={!frame && !busy} onClick={() => { sim.reset(); setPlacing(false); setSelectedCell(null); }}>↺ Reset scenario</button></div>
          <div className="timeline"><input id="timeline" type="range" min={first} max={Math.max(first + stepHours / 12, last)} value={frame?.state.step_index ?? 0} step={stepHours / 12} disabled={scenario.history.length < 2} aria-label={daily ? "Inspect a historical comparison day" : "Inspect a simulated twelve-hour step"} aria-valuetext={`Step ${frame?.state.step_index ?? 0}, ${frame?.elapsed_hours ?? 0} simulated hours`} onChange={e => sim.seek(Number(e.target.value))} />
            <div className="timeline-labels" aria-hidden="true">{Array.from({ length: 5 }, (_, i) => {
              const step = Math.round(first + (last - first) * i / 4);
              return <span key={i}>{(last - first < 4 && i > 0 && i < 4) || (!last && i > 0) ? '' : step ? `+${step * 12} h` : 'Start'}</span>;
            })}</div></div>
          <div className="playback-bottom"><div className="transport"><button id="play" className="play-button" disabled={!ready || !frame || frame.finished} onClick={togglePlay}>{playing || busy ? 'Ⅱ Pause' : '▶ Play'}</button><button id="step" className="step-button" disabled={!ready || !frame || frame.finished || !!busy || playing} aria-label={daily ? "Advance one day" : "Advance twelve hours"} onClick={() => { sim.pause(); setPlacing(false); sim.advance(); }}>+{stepHours} h →</button></div>
            <label className="speed-label" htmlFor="speed">Step interval <select id="speed" value={sim.speed} onChange={e => sim.setSpeed(Number(e.target.value))}>{[1, 3, 5, 10].map(speed => <option key={speed} value={speed}>{speed} sec</option>)}</select></label>
            <span id="playback-state" className="playback-state" role="status">{busy ? busy === 'step' ? 'PREDICTING' : 'LOADING' : playing ? 'PLAYING' : 'PAUSED'}</span></div>
        </section>
      </section>
    </main>
    <dialog id="help-dialog" ref={help} aria-labelledby="help-title"><button id="close-help" className="icon-button" aria-label="Close instructions" onClick={() => help.current.close()}>×</button>
      <h2 id="help-title">Follow a possible fire.</h2>
      <ol><li>Place fires on the map or by coordinates, or load current FIRMS observations.</li><li>Play or advance twelve hours. Pause freezes the visible state, including during a request. Switching tabs pauses playback.</li><li>Use the timeline to revisit the latest 128 completed frames. Use the cell list to inspect every cell with a keyboard.</li></ol>
      <h3>Historical comparison</h3><p>Under FIRMS detections, enable Historical mode and choose May 11–August 21, 2026. Purple rings show the entire selected UTC day's retained detections, combined into 1 km cells. Orange fire is initialized only once from eligible observations before that day's closing midnight. The timeline timestamp is this end-of-day cutoff. Each advance runs two 12-hour predictions and loads the next day's observations, with the original loading area preserved. Later observations never reseed the simulation. Playback stops after August 21. These retrospective observations are not a reconstructed real-time feed; missing detections do not prove an absence of fire.</p>
      <h3>How fire becomes burned</h3><p>The 1 km model assigns a fuel duration from vegetation type, mapped vegetated area and usable canopy density at scenario start. More estimated fuel lasts longer; the assigned duration stays fixed as fuel is consumed. Missing vegetation uses an explicit {config?.transition?.fuel_policy?.fallback_hours ?? 24}-hour fallback. Inspect an active cell for its duration and evidence basis. Updates occur every 12 hours, so shorter burns appear burned at the next update. Polygon patches use fuel-type-specific burning durations{config?.local_spread?.policy?.residence_minutes_by_fuel && <> ({Math.min(...Object.values(config.local_spread.policy.residence_minutes_by_fuel))}–{Math.max(...Object.values(config.local_spread.policy.residence_minutes_by_fuel))} simulated minutes on this server)</>}, measured from each patch's ignition. A 1 km summary cell can contain both active and burned patches. Historical playback advances two 12-hour steps per day. Burned fuel does not reignite in the same scenario.</p>
      <h3>Research limitations</h3><p>One possible simulation, not an operational forecast. Cover-based fuel amounts and burning durations are uncalibrated scenario assumptions; dead wood, litter and fuel moisture are not measured. The 1 km model has no weather inputs; polygon spread uses constant scenario wind. Intensity is a scenario scale, not fire radiative power. Mapped water excludes whole 1 km cells; unresolved waterways may still be missed. Evaluation covers 12–96 hours; longer playback does not establish accuracy. Follow local emergency authorities.</p>
      <h3>Data and attribution</h3><p>Observations: <a href="https://www.earthdata.nasa.gov/data/tools/firms">NASA FIRMS</a>, VIIRS SNPP, NOAA-20 and NOAA-21 NRT. Context where available: NOAA ETOPO 2022, NALCMS land cover, NASA MOD44B and MOD13Q1. Water barriers: <a href="https://www.naturalearthdata.com/about/terms-of-use/">Natural Earth</a>. Optional basemap: <a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors, ODbL</a>.</p>
      <h3>Privacy</h3><p>Scenarios live in this tab’s memory and clear on reload. Coordinates and scenario states are sent to this app’s server for calculation. Live FIRMS requests send the selected bounds to NASA through the server; historical mode reads the server’s retained archive. This app adds no analytics, cookies, or browser storage. Hosting providers may retain access logs. The OpenStreetMap basemap sends your IP, site origin, and tile coordinates to its provider; it loads automatically and can be disabled under Map layers.</p>
      <p><a href="/static/THIRD_PARTY_LICENSES.txt">React and Leaflet license notices</a>. A code license does not grant rights to every dataset or third-party service.</p>
    </dialog>
  </>;
}
