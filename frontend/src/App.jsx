import { useEffect, useRef, useState } from 'react';
import FireMap from './FireMap';
import Inspector from './Inspector';
import HistoricalInspector from './HistoricalInspector';
import CellList from './CellList';
import { installedRegionAt, shortRegionName, placementRoute, regionForView } from './coverage';
import { useScenario } from './useScenario';
import { selectionKey } from './mapCells';

export default function App() {
  const sim = useScenario();
  const { config, frame, scenario, playing, busy, status } = sim;
  const [mode, setMode] = useState('place');
  const [placing, setPlacing] = useState(false);
  const [coordinates, setCoordinates] = useState({ latitude: '53.02', longitude: '-117.31' });
  const [historicalMode, setHistoricalMode] = useState(false);
  const [historicalDate, setHistoricalDate] = useState('2026-05-11');
  const [basemap, setBasemap] = useState(true);
  const [referenceLayers, setReferenceLayers] = useState([]);
  const [focusedRegion, setFocusedRegion] = useState(null);
  const [view, setView] = useState({ longitude: -105, latitude: 44, zoom: 4 });
  const [connected, setConnected] = useState(navigator.onLine);
  const [mapConnection, setMapConnection] = useState('checking');
  const [visibility, setVisibility] = useState({ active: true, burned: true, candidate: false, historical: true });
  const [selectedCell, setSelectedCell] = useState(null);
  const mapApi = useRef(null), help = useRef(null), inspectorHeading = useRef(null), returnFocus = useRef(null);
  const points = [...(frame?.points || []), ...(frame?.historical?.points || [])];
  const selected = points.find(p => selectionKey(p, frame?.local) === selectedCell);
  const daily = !!frame?.historical;
  const stepHours = daily ? 24 : 12;
  const historicalDates = config?.historical_firms;
  const regions = config?.local_spread?.regions;
  const selectedPreset = null;
  const selectedRegion = regions?.find(region => region.id === (sim.localRegion || focusedRegion));
  const viewRegion = installedRegionAt(regions || [], view.longitude, view.latitude);
  const onlineAllowed = connected && (!config?.desktop || config.desktop.online_enabled);
  const showBasemap = !!config && basemap && onlineAllowed;
  // Routine seed/step/reset notices duplicate the sidebar and obscure the map.
  // Keep the map live region for actionable errors only.
  const mapError = status.error ? status.text : '';
  useEffect(() => {
    const online = () => setConnected(navigator.onLine);
    window.addEventListener('online', online); window.addEventListener('offline', online);
    return () => { window.removeEventListener('online', online); window.removeEventListener('offline', online); };
  }, []);
  useEffect(() => {
    document.body.classList.toggle('embedded-desktop', !!config?.desktop && window.parent !== window);
  }, [!!config?.desktop]);
  useEffect(() => {
    window.parent.postMessage({ type: 'wildfire:map-connection', state: mapConnection }, location.origin);
  }, [mapConnection]);
  useEffect(() => {
    if (!regions) return;
    setReferenceLayers([]);
    const controller = new AbortController();
    for (const region of regions.filter(region => !region.tiled)) fetch(`/api/local/regions/${encodeURIComponent(region.id)}/layers`, { signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error(`Map detail unavailable for ${shortRegionName(region)}.`); return response.json(); })
      .then(pack => setReferenceLayers(previous => [...previous, pack]))
      .catch(error => { if (error.name !== 'AbortError') sim.message(error.message, true); });
    return () => controller.abort();
  }, [regions, sim.message]);
  useEffect(() => {
    if (regions?.[0]?.example_ignition) setCoordinates({
      latitude: String(regions[0].example_ignition.latitude), longitude: String(regions[0].example_ignition.longitude),
    });
  }, [regions]);
  const focusRegion = region => {
    setFocusedRegion(region.id); setPlacing(false);
    mapApi.current?.fitRegion(region.bounds);
    if (region.example_ignition) setCoordinates({ latitude: String(region.example_ignition.latitude), longitude: String(region.example_ignition.longitude) });
  };
  const validHistoricalDate = historicalDate.length === 10 && historicalDate >= (historicalDates?.min_date ?? '2026-05-11') && historicalDate <= (historicalDates?.max_date ?? '2026-08-21');
  const ready = !!(config?.local_spread?.available || config?.model_ready);
  const canPlace = !!config && !busy && !playing && (!frame || (frame.state.step_index === 0 && scenario.source === 'placed'));
  const first = scenario.history[0]?.state.step_index ?? 0, last = scenario.history.at(-1)?.state.step_index ?? 0;
  const inspect = (point, trigger) => {
    sim.pause(); setPlacing(false);
    returnFocus.current = trigger || document.getElementById('map');
    setSelectedCell(selectionKey(point, frame?.local));
    if (selectionKey(point, frame?.local) === selectedCell) inspectorHeading.current?.focus();
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
  const add = async (lat, lon) => {
    if (!canPlace) return false;
    const region = installedRegionAt(regions || [], lon, lat);
    const route = placementRoute({ region, frame, online: onlineAllowed, modelReady: config?.model_ready });
    if (route.error) { sim.message(route.error, true); return false; }
    const accepted = await sim.addIgnition(lat, lon, 1, route.endpoint);
    if (accepted) setFocusedRegion(region?.id || null);
    return accepted;
  };
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
    const bounds = {
      west: Math.max(west, view.getWest()), south: Math.max(south, view.getSouth()),
      east: Math.min(east, view.getEast()), north: Math.min(north, view.getNorth()),
    };
    sim.message(sim.expanding ? 'Loading satellite observations and preparing local fuel and road tiles. First use may take several minutes…' : historicalMode ? `Loading retained FIRMS for ${historicalDate}…` : 'Loading current observations from three VIIRS satellites…');
    const viewRegion = regionForView(regions || [], bounds);
    const regional = viewRegion?.tiled ? 'landscape' : !!viewRegion;
    if (!regional && !onlineAllowed) { sim.message('You are offline. Zoom to an installed region to use retained local data.', true); return; }
    await sim.loadFirms(bounds, historicalMode ? historicalDate : null, regional);
  }
  const switchMode = value => { sim.pause(); setPlacing(false); setMode(value); };
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
        <p className="research-label">Research only · not a forecast</p>
        <div className="tabs" role="group" aria-label="Starting state source">
          <button id="place-tab" className={`tab ${mode === 'place' ? 'selected' : ''}`} aria-pressed={mode === 'place'} aria-controls="place-panel" onClick={() => switchMode('place')}>Place a fire</button>
          <button id="firms-tab" className={`tab ${mode === 'firms' ? 'selected' : ''}`} aria-pressed={mode === 'firms'} aria-controls="firms-panel" onClick={() => switchMode('firms')}>FIRMS detections</button>
        </div>
        <section id="installed-regions" aria-label="Installed regions">
          <div className="label-row"><h2>Installed regions</h2><button className="text-button" onClick={() => { sim.pause(); help.current.showModal(); }}>Details</button></div>
          <div className="region-links">{regions?.map(region => <button key={region.id} data-region={region.id} className="region-link" onClick={() => focusRegion(region)}><span>{shortRegionName(region)}</span><small>Installed · {region.area_km2?.toFixed(0)} km² ↗</small></button>)}</div>
          {config && !regions?.length && <p className="hint">No regional data installed.</p>}
          <p id="polygon-coverage-note" className="hint">{frame?.local ? `${shortRegionName(selectedRegion)} · 100 m fuel patches` : frame ? '1 km research simulation · detailed fuel/road data not installed' : onlineAllowed && config?.model_ready ? '100 m fuel patches inside outlines; 1 km research simulation elsewhere on supported North American land.' : 'Place inside an outlined region to simulate offline.'}</p>
        </section>
        {config?.local_spread?.expanding_error && <p className="hint">Expanding fuel and road simulation is unavailable on this server.</p>}
        {config?.local_spread?.region_errors?.map(error => <p className="hint" key={error}>{error}</p>)}
        {Object.keys(config?.data_preparation?.errors || {}).length > 0 && <p className="hint">Some vegetation or polygon data could not be prepared. Check the server startup logs, then restart to retry.</p>}
        <section id="place-panel" hidden={mode !== 'place'} aria-label="Place starting fires">

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
          <p className="panel-copy">Load NASA detections in the current view. Zoom to an installed region for fuel-patch simulation; broader views use the 1 km research grid.</p>
          {sim.expanding && <p className="hint">Zoom in around the fire before loading. Detailed scenarios support up to 500 starting cells{config?.local_spread?.limits && <> across {config.local_spread.limits.max_tiles} tiles ({config.local_spread.limits.max_area_km2.toLocaleString()} km²), subject to the fuel-patch budget</>}. To load all North America, select the 1 km spread model.</p>}
          <label className="layer-toggle" htmlFor="historical-mode"><span>Historical mode</span><input id="historical-mode" type="checkbox" checked={historicalMode} onChange={e => { sim.pause(); setHistoricalMode(e.target.checked); }} /></label>
          {historicalMode && <div className="historical-controls">
            <label htmlFor="historical-date">Observation date (UTC)</label>
            <input id="historical-date" type="date" min={historicalDates?.min_date ?? '2026-05-11'} max={historicalDates?.max_date ?? '2026-08-21'} value={historicalDate} required onChange={e => { sim.pause(); setHistoricalDate(e.target.value); }} />
            <p className="hint">Installed regions use an ignition snapshot; broader views use the legacy daily grid comparison.</p>
          </div>}
          <button id="load-firms" className="primary-button" disabled={!ready || !!busy || playing || sim.firmsCooldown > 0 || (historicalMode ? !historicalDates?.available || !validHistoricalDate : !config?.firms_configured || !onlineAllowed)} onClick={loadFirms}>{busy?.includes('firms') ? 'Loading satellites…' : sim.firmsCooldown > 0 ? `Retry in ${sim.firmsCooldown} s` : historicalMode ? '↓ Load historical FIRMS' : '↓ Load current FIRMS'}</button>
          {!onlineAllowed && !historicalMode && <p className="hint">Current detections need internet. Local simulations remain available; connection recovery is automatic.</p>}
          {config && !(historicalMode ? historicalDates?.available : config.firms_configured) && <p className="hint">{historicalMode ? 'The retained historical FIRMS archive is unavailable on this server.' : 'Satellite loading is unavailable on this server. You can still place a fire.'}</p>}
          <p className="hint">{historicalMode ? 'Seeds once from observations 3–24 hours before the selected day’s closing midnight. Uses the installed landscape.' : 'Observations are 3–24 hours old. Fine ignition positions are approximated from 1 km detection cells.'} Loading replaces this scenario.</p>
        </section>
        <div className="sidebar-divider" />
        <div className="label-row"><h2>Scenario overview</h2><span id="state-badge" className="badge">{playing ? 'RUNNING' : frame ? 'PAUSED' : 'READY'}</span></div>
        <div className="stats"><div><strong id="active-count">{frame?.active_count ?? 0}</strong><span>Active cells</span></div><div><strong id="burned-count">{frame?.burned_count ?? 0}</strong><span>Burned cells</span></div></div>
        <div className="scenario-detail"><span>Simulated time</span><strong id="elapsed">+{frame?.elapsed_hours ?? 0} hours</strong></div>
        <div className="scenario-detail"><span>New ignitions</span><strong id="new-count">{frame?.new_ignition_count ?? '—'}</strong></div>
        {frame?.weather_ml && <div id="weather-summary" className="historical-summary" role="status">
          <strong>Weather ML · {frame.metadata.hybrid.weather.extended ? 'Beyond forecast — assumed conditions' : frame.metadata.hybrid.weather.mode === 'historical' ? 'Historical analysis' : 'Captured forecast'}</strong>
          <p>{frame.metadata.hybrid.weather.temperature_c.toFixed(1)} °C · {frame.metadata.hybrid.weather.humidity_percent.toFixed(0)}% humidity · {Math.hypot(frame.metadata.hybrid.weather.wind_east_m_s, frame.metadata.hybrid.weather.wind_north_m_s).toFixed(1)} m/s wind. Open-Meteo / ECMWF IFS.</p>
          <p>{frame.metadata.hybrid.weather.extended ? 'Weather conditions are held beyond available coverage; this is no longer forecast-driven.' : `Weather captured through ${new Date(frame.metadata.hybrid.weather.available_through).toLocaleString()}.`} Experimental 1 km probabilities guide detailed spread; this is not a validated perimeter forecast.</p>
        </div>}
        {daily && <div className="historical-summary" role="status"><strong>Historical comparison · {frame.historical.date} UTC</strong><p>{frame.historical.detection_count} observed detections in {frame.historical.cell_count} cells. {frame.finished ? 'End of available dates.' : 'Daily observations advance with predictions.'}</p></div>}
        <div className="scenario-detail"><span>Simulation detail</span><strong id="simulation-detail">{frame ? frame.local ? `${config?.local_spread?.mesh_m} m fuel patches` : '1 km research grid' : 'Automatic by location'}</strong></div>
        {frame && !frame.local && <p id="coarse-limitations" className="hint">Grid-cell footprints, not fine-scale fire perimeters. No local fuel/road barriers or live weather.{frame.terrain_missing_count > 0 && <> Terrain missing for {frame.terrain_missing_count} evaluated cells; trained missing-input handling is used.</>}</p>}
        {frame?.local && <p className="hint">{(frame.active_area_m2 / 10000).toFixed(1)} active ha · {(frame.burned_area_m2 / 10000).toFixed(1)} burned ha. Counts summarize 1 km cells; a cell may contain both active and burned patches.</p>}
        {frame?.boundary_reached && <p id="boundary-warning" className="hint" role="status">Installed coverage boundary reached. Spread beyond it is not modeled.</p>}
        {selectedRegion?.tiled && <p id="regional-limitations" className="hint">Full {selectedRegion.label} data installed · 30 m land cover; {config?.local_spread?.mesh_m} m simulation mesh. Local runs load small tiles as needed, up to {config?.local_spread?.limits?.max_area_km2} km² and {config?.local_spread?.limits?.max_patches?.toLocaleString()} fuel patches. Purple cover is urban/unknown, not safe.</p>}
        {frame?.metadata?.unsupported_observed_cells > 0 && <p id="unsupported-observations" className="hint">{frame.metadata.unsupported_observed_cells} observed cells excluded: no supported fuel data.</p>}
        <div className="sidebar-divider" /><h2>Map layers</h2>
        <p className="hint"><button id="view-us" className="secondary-button" onClick={() => {
          setPlacing(false); mapApi.current?.fitRegion([-125, 24, -66, 50]);
        }}>US overview</button></p>
        <p className="hint">Drag to pan; scroll to zoom. Map browsing is unrestricted; simulation and data coverage are separate.</p>
        {Object.entries({ active: 'Active fire', burned: 'Burned cells', candidate: 'Spread candidates' }).map(([key, label]) =>
          <label className="layer-toggle" key={key}><span><i className={`legend-dot ${key}`} aria-hidden="true" />{label}</span><input id={`show-${key === 'candidate' ? 'candidates' : key}`} type="checkbox" checked={visibility[key]} onChange={e => setVisibility(value => ({ ...value, [key]: e.target.checked }))} /></label>)}
        {daily && <label className="layer-toggle"><span><i className="legend-dot historical" aria-hidden="true" />Historical FIRMS (purple)</span><input id="show-historical" type="checkbox" checked={visibility.historical} onChange={e => setVisibility(value => ({ ...value, historical: e.target.checked }))} /></label>}
        <label className="layer-toggle"><span>OpenStreetMap basemap</span><input id="show-basemap" type="checkbox" checked={showBasemap} disabled={!onlineAllowed} onChange={e => setBasemap(e.target.checked)} /></label>
        {(!showBasemap || mapConnection !== 'online') && <p className="hint">Offline overview · detail inside installed regions only.</p>}
        <p className="hint">The basemap sends your IP address, site origin, and viewed map tiles to OpenStreetMap. Turn it off to stop loading tiles. <a href="https://osmfoundation.org/wiki/Privacy_Policy">Provider privacy policy</a>.</p>
        <CellList points={points} onInspect={inspect} />
        <div className="sidebar-footer"><div><strong id="model-name">{frame && !frame.local ? 'Reference 1 km research model' : 'Polygon travel engine (uncalibrated)'}</strong><p>Experimental spread · <a href="/static/THIRD_PARTY_LICENSES.txt">Software notices</a></p></div></div>
      </aside>
      <section className="map-workspace" aria-label="Map and simulation playback">
        <FireMap region={selectedRegion} regions={regions} referenceLayers={referenceLayers} frame={frame} selectedCell={selectedCell} visibility={visibility} placing={placing && canPlace} basemap={showBasemap} mapApi={mapApi} onPlace={add} onInspect={inspect} onRegion={focusRegion} onView={setView} onConnection={setMapConnection} onGroup={() => { sim.pause(); setPlacing(false); }} onError={text => sim.message(text, true)} />
        <div className="map-title"><span id="map-mode">{mapConnection === 'online' && showBasemap ? 'Online map' : 'Offline overview'}</span><strong id="map-coverage">{frame && !frame.local ? '1 km research grid · not detailed fuel spread' : viewRegion ? `${shortRegionName(viewRegion)} · installed` : view.zoom < 9 ? `${regions?.length || 0} installed regions outlined` : onlineAllowed && config?.model_ready ? '1 km exploration · no detailed pack here' : 'Regional data not installed here'}</strong></div>
        <button id="fit" className="map-button" onClick={fit}>Fit fires</button>
        <div id="map-instruction" className="map-instruction">{placing ? 'Click the map to add starting fire cells.' : frame?.local ? 'Click a 1 km square to highlight it and inspect fire and vegetation data.' : frame ? 'Inspect cells on the map or in the scenario cell list.' : 'Start with a fire or current satellite detections.'}</div>
        <div id="status" className={`status ${mapError ? 'error' : 'empty'}`} role="status" aria-live="polite" aria-atomic="true">{mapError}</div>
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
      <h3>Current simulation</h3>
      {selectedRegion && !selectedPreset && <p>{selectedRegion.label}: {selectedRegion.tiled ? 'full province/state coverage, loaded as small local tiles' : `fixed ${selectedRegion.area_km2?.toFixed(0)} km² pack`}. Spread stops at the dashed coverage boundary. Water, urban mixtures and unknown cover are not supported ignition locations. The example coordinates provide a supported starting location.</p>}
      {selectedPreset && <p>{selectedPreset.label} view. Fuel and road coverage loads around each fire as it spreads.</p>}
      {selectedRegion?.sources?.length > 0 && <p>Historical sources: {selectedRegion.sources.map(s => `${s.product} ${s.component_year || s.version || s.release || 'date unknown'}`).join(' · ')}. Not current fuel conditions. {selectedRegion.tiled ? 'Unknown road widths and grades are preserved in the local data.' : `Road width unknown for ${selectedRegion.unknown_width_count}/${selectedRegion.road_count}; grade unknown for ${selectedRegion.unknown_grade_count}/${selectedRegion.road_count}.`} General ember crossing is not modeled.</p>}
      {sim.localRegion && <p>Experimental {config.local_spread.mesh_m} m fuel patches with road barriers. Travel rates are uncalibrated and wind is a constant scenario assumption, not live weather. Urban mixtures and structures are unsupported. Unknown road widths use a {config.local_spread.policy.unknown_road_width_m} m assumption where surface type is mapped. Polygon ignition starts a supported fuel patch; the 1 km model's intensity slider is not used. {sim.expanding ? 'Roads are read from a local archive. Landscape tiles are built as the fire spreads; first preparation may take several minutes.' : 'Place within the selected region.'}</p>}
      <h3>Automatic simulation and offline maps</h3><p>Region buttons and map labels move the view; they do not erase a run. Ignitions inside a verified pack use its 100 m fuel-patch engine. When connected, locations outside those packs use the labelled 1 km research model on supported North American land. Its polygons are grid-cell footprints, not fine-scale fire perimeters; it has no local road barriers, measured fuel cover or live weather. Terrain is incomplete and missing inputs use the model's trained handling. Manual grid ignitions start at scenario strength 1, not a measured intensity or probability. Reset before changing engines or packs; an existing grid run can continue locally after disconnection. Offline, new ignitions outside installed packs are blocked. Internet access does not install detailed packs. Unknown and urban cover inside a pack remain unsupported, not safe.</p>
      <h3>Satellite observations</h3><p>A close-up view centred on one installed region seeds fuel patches from observed 1 km cell centres; unsupported cells are counted and excluded. Broader views use the labelled 1 km model. Historical mode, where an archive is installed, uses an ignition snapshot for fuel patches, or a daily observation comparison for the grid model. Neither reconstructs historical fuel conditions. Missing detections do not prove an absence of fire.</p>
      <h3>How fire becomes burned</h3><p>Polygon spread tracks each fuel patch's ignition time separately; a patch becomes burned after its configured burning duration{config?.local_spread?.policy && <> ({config.local_spread.policy.residence_minutes} simulated minutes on this server)</>}. A 1 km summary cell can contain both active and burned patches. Burned fuel does not reignite in the same scenario.</p>
      <h3>Research limitations</h3><p>One possible simulation, not an operational forecast. Do not use for evacuation or emergency decisions; follow local emergency authorities. Burning durations are scenario assumptions, not measured fuel depletion. The 1 km model has no weather inputs; polygon spread uses constant scenario wind. Intensity is a scenario scale, not fire radiative power. Small waterways may be missed. Evaluation covers 12–96 hours; longer playback does not establish accuracy.</p>
      <h3>Data and attribution</h3><p>Observations: <a href="https://www.earthdata.nasa.gov/data/tools/firms">NASA FIRMS</a>, VIIRS SNPP, NOAA-20 and NOAA-21 NRT. Context where available: NOAA ETOPO 2022, NALCMS land cover, NASA MOD44B and MOD13Q1. Water barriers: <a href="https://www.naturalearthdata.com/about/terms-of-use/">Natural Earth</a>. Optional basemap: <a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors, ODbL</a>.</p>
      <h3>Privacy</h3><p>Scenarios live in this tab’s memory and clear on reload. Coordinates and scenario states are sent to this app’s server for calculation. Live FIRMS requests send the selected bounds to NASA through the server; historical mode reads the server’s retained archive. This app adds no analytics, cookies, or browser storage. Hosting providers may retain access logs. The OpenStreetMap basemap sends your IP, site origin, and tile coordinates to its provider; it loads automatically and can be disabled under Map layers.</p>
      <p><a href="/static/THIRD_PARTY_LICENSES.txt">React and Leaflet license notices</a>. A code license does not grant rights to every dataset or third-party service.</p>
    </dialog>
  </>;
}
