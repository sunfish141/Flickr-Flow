import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { selectionKey, polygonCellVisible } from './mapCells';
import { shortRegionName } from './coverage';

export default function FireMap({ region, regions, referenceLayers, frame, selectedCell, visibility, placing, basemap, mapApi, onPlace, onInspect, onRegion, onView, onConnection, onGroup, onError }) {
  const container = useRef(null);
  const state = useRef(null);
  const latest = useRef(null);
  latest.current = { region, regions, referenceLayers, basemap, frame, selectedCell, visibility, placing, onPlace, onInspect, onRegion, onView, onConnection, onGroup, onError };
  useEffect(() => {
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const map = L.map(container.current, { zoomControl: false, preferCanvas: true, minZoom: 2, maxZoom: 16,
      zoomAnimation: !reduced, fadeAnimation: !reduced, markerZoomAnimation: !reduced,
      // Browsing is not simulation coverage. Constraining the whole viewport to
      // North America pinned its center far north at overview zooms and snapped
      // southward drags back. Leave navigation free, including wrapped longitudes.
      worldCopyJump: true }).setView([44, -105], 4);
    L.control.zoom({ position: 'topright' }).addTo(map);
    const cellRenderer = L.svg({ padding: .2 });
    map.createPane('offlineOverview'); map.getPane('offlineOverview').style.zIndex = '190';
    map.createPane('regionalMaps'); map.getPane('regionalMaps').style.zIndex = '195';
    const regionalMaps = new Map();
    const failedRegionalMaps = new Set();
    function regionalMapState() {
      const active = [...regionalMaps.values()].filter(layer => map.hasLayer(layer));
      container.current.dataset.regionalMap = active.some(layer => failedRegionalMaps.has(layer)) ? 'error'
        : active.some(layer => layer.isLoading()) ? 'loading' : active.length ? 'ready' : 'hidden';
    }
    const coverageRenderer = L.svg();
    const overview = L.layerGroup().addTo(map);
    const layers = { reference: L.layerGroup().addTo(map), coverage: L.layerGroup().addTo(map), roads: L.layerGroup().addTo(map), burned: L.layerGroup().addTo(map), active: L.layerGroup().addTo(map), candidate: L.layerGroup().addTo(map), cells: L.layerGroup().addTo(map), selection: L.layerGroup().addTo(map), historical: L.layerGroup().addTo(map) };
    let healthy = false, connection = '', retryTimer, deadline, disposed = false;
    map.attributionControl.addAttribution('<a href="https://www.naturalearthdata.com/about/terms-of-use/">Natural Earth</a> · generalized overview');
    const controller = new AbortController();
    fetch('/static/offline-overview.geojson', { signal: controller.signal }).then(response => {
      if (!response.ok) throw new Error('Offline overview could not be loaded.');
      return response.json();
    }).then(data => {
      if (disposed) return;
      L.geoJSON(data, { pane: 'offlineOverview', interactive: false, style: feature => ({
        fillColor: feature.properties.kind === 'land' ? '#e0e6d6' : '#c8dfe8', fillOpacity: 1,
        color: '#9eafa4', weight: .7,
      }) }).addTo(overview);
      for (const [name, lat, lon] of [['CANADA', 57, -105], ['UNITED STATES', 38, -99], ['MEXICO', 26, -104]]) {
        const label = document.createElement('span'); label.textContent = name;
        L.marker([lat, lon], { pane: 'offlineOverview', interactive: false,
          icon: L.divIcon({ className: 'overview-label', html: label, iconSize: [120, 20], iconAnchor: [60, 10] }) }).addTo(overview);
      }
      container.current.dataset.overview = 'ready';
    }).catch(error => { if (error.name !== 'AbortError') latest.current.onError(error.message); });
    const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    });
    function report(value) {
      if (value !== connection) { connection = value; latest.current.onConnection(value); }
      container.current.dataset.connection = value;
    }
    function fallback() {
      if (disposed || !latest.current.basemap) return;
      clearTimeout(deadline); healthy = false; tiles.remove(); report('unavailable'); draw();
      if (!retryTimer) retryTimer = setTimeout(() => { retryTimer = null; if (latest.current.basemap) startTiles(); }, 30000);
    }
    function startTiles() {
      clearTimeout(deadline); clearTimeout(retryTimer); retryTimer = null;
      report('checking'); tiles.addTo(map);
      deadline = setTimeout(fallback, 8000);
    }
    tiles.on('tileerror', fallback);
    tiles.on('tileload', () => {
      if (!latest.current.basemap || !map.hasLayer(tiles)) return;
      clearTimeout(deadline);
      if (!healthy) { healthy = true; draw(); }
      report('online');
    });
    function draw() {
      Object.values(layers).forEach(layer => layer.clearLayers());
      const { region, frame, visibility, selectedCell } = latest.current;
      for (const installed of latest.current.regions || []) {
        if (!installed.map_tiles) continue;
        if (!regionalMaps.has(installed.id)) {
          const [w, s, e, n] = installed.bounds;
          const layer = L.tileLayer(installed.map_tiles, {
            pane: 'regionalMaps', bounds: [[s, w], [n, e]], minZoom: 2, maxZoom: 16,
            keepBuffer: 1, updateWhenIdle: true, attribution: 'NALCMS · Overture Maps contributors',
          });
          layer.on('loading', () => { failedRegionalMaps.delete(layer); regionalMapState(); });
          layer.on('load', regionalMapState);
          layer.on('tileerror', () => {
            if (healthy || !map.hasLayer(layer)) return;
            failedRegionalMaps.add(layer); regionalMapState();
            latest.current.onError('A local map tile could not be loaded. Try zooming again; if it persists, check the installed regional data.');
          });
          regionalMaps.set(installed.id, layer);
        }
        const layer = regionalMaps.get(installed.id);
        if (!healthy && !map.hasLayer(layer)) layer.addTo(map);
        else if (healthy && map.hasLayer(layer)) layer.remove();
      }
      regionalMapState();
      if (!healthy) {
        const colors = { water: '#96c8dd', urban: '#b08baa', unknown: '#b08baa', barren: '#c7bfb3', snow_ice: '#e1eaed',
          needleleaf: '#51745c', broadleaf: '#70935b', mixed_forest: '#64816b', shrubland: '#a7b481', grassland: '#c3cd97', wetland: '#83aea6', cropland: '#d8cf96' };
        for (const pack of latest.current.referenceLayers || []) for (const name of ['cover', 'unknown', 'roads']) L.geoJSON(pack.layers[name], {
          interactive: false, style: feature => name === 'roads' ? { color: '#515d66', weight: 1, opacity: .6 }
            : { fillColor: colors[feature.properties.fuel] || '#9aaf88', fillOpacity: .65, weight: 0 },
        }).addTo(layers.reference);
      }
      for (const installed of latest.current.regions || []) {
        L.geoJSON(installed.coverage, { interactive: false, renderer: coverageRenderer,
          style: { className: 'installed-boundary', color: '#21627f', weight: installed.id === (frame?.region_id || frame?.state.region) ? 3 : 2, dashArray: '6 4', fill: false } }).addTo(layers.coverage);
        const [west, south, east, north] = installed.bounds;
        const label = document.createElement('button');
        label.type = 'button'; label.textContent = shortRegionName(installed);
        label.setAttribute('aria-label', `Go to installed region ${shortRegionName(installed)}`);
        label.dataset.region = installed.id;
        L.DomEvent.disableClickPropagation(label);
        label.addEventListener('click', () => latest.current.onRegion(installed));
        L.marker([north, (west + east) / 2], { keyboard: false,
          icon: L.divIcon({ className: 'installed-map-label', html: label, iconSize: [160, 30], iconAnchor: [80, 32] }) }).addTo(layers.coverage);
      }
      if (!frame) return;
      const view = map.getBounds().pad(.1);
      if (frame.local) {
        L.geoJSON(frame.roads, { style: { color: '#454a50', weight: 2, opacity: .6 },
          onEachFeature(feature, layer) {
            const label = document.createElement('span');
            label.textContent = `${feature.properties.class || 'Road'} · ${feature.properties.width_m == null ? 'width unknown' : `${feature.properties.width_m} m recorded width`}`;
            layer.bindTooltip(label);
          },
        }).addTo(layers.roads);
        for (const feature of frame.perimeters.features) {
          const status = feature.properties.status;
          if (!visibility[status]) continue;
          L.geoJSON(feature, { interactive: false, style: {
            color: status === 'active' ? '#b83e21' : '#5d5a52', weight: 1,
            fillColor: status === 'active' ? '#ee713b' : '#767169', fillOpacity: .55,
          } }).addTo(layers[status]);
        }
        for (const point of frame.points) {
          if (!point.cell_geometry || !polygonCellVisible(point, visibility)) continue;
          const cell = L.geoJSON(point.cell_geometry, { renderer: cellRenderer, bubblingMouseEvents: false,
            style: { className: 'polygon-cell', color: '#53676b', weight: 1, opacity: .45,
              dashArray: '3 4', fill: true, fillOpacity: 0 },
          });
          if (!view.intersects(cell.getBounds())) continue;
          const tooltip = document.createElement('span');
          tooltip.textContent = '1 km² fire cell · click to inspect';
          cell.bindTooltip(tooltip).on('click', () => latest.current.onInspect(point)).addTo(layers.cells);
          cell.eachLayer(layer => layer.getElement()?.setAttribute('data-cell-id', point.cell_id));
          if (selectedCell === selectionKey(point, true)) {
            L.geoJSON(point.cell_geometry, { renderer: cellRenderer, interactive: false,
              style: { className: 'selected-polygon-cell', color: '#087b94', weight: 3,
                opacity: 1, fillColor: '#31a8c0', fillOpacity: .12 },
            }).addTo(layers.selection);
          }
        }
      }
      const visible = [...frame.points, ...(frame.historical?.points || [])].filter(p => view.contains([p.latitude, p.longitude]));
      const groups = new Map();
      for (const point of visible) {
        if (!visibility[point.status] || (frame.local && point.status !== 'historical')) continue;
        const pixel = map.project([point.latitude, point.longitude]);
        const pointKey = selectionKey(point, frame.local);
        const key = visible.length > 1500 && map.getZoom() < 11 && pointKey !== selectedCell ?
          `${point.status}:${Math.floor(pixel.x / 32)}:${Math.floor(pixel.y / 32)}` : pointKey;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(point);
      }
      for (const group of groups.values()) {
        const point = group[0];
        const active = point.status === 'active', candidate = point.status === 'candidate', historical = point.status === 'historical';
        const color = historical ? '#6536b3' : active ? '#b83e21' : candidate ? '#806229' : '#5d5a52';
        const style = {
          ...(frame.local ? { renderer: cellRenderer } : {}),
          radius: group.length > 1 ? Math.min(20, 7 + Math.log2(group.length)) + (historical ? 4 : 0) : historical ? 12 : active ? 5 + point.intensity * 4 : 5,
          color, weight: selectedCell === selectionKey(point, frame.local) ? 4 : historical ? 2 : 1,
          fillColor: active ? `hsl(${12 + (1 - point.intensity) * 24}, 85%, 55%)` : candidate ? '#efd5a6' : '#767169',
          fillOpacity: historical ? 0 : candidate ? .45 : .88, bubblingMouseEvents: false,
        };
        // Coarse predictions are the actual 1 km grid footprints, never a
        // circle implying a physical fire radius. Observation markers remain
        // point symbols, and installed fuel patches retain their own geometry.
        const marker = historical ? L.circleMarker([point.latitude, point.longitude], style)
          : L.geoJSON({ type: 'FeatureCollection', features: group.filter(p => p.geometry).map(p => ({
              type: 'Feature', geometry: p.geometry, properties: { cell_id: p.cell_id },
            })) }, { style, bubblingMouseEvents: false });
        const tooltip = document.createElement('span');
        tooltip.textContent = group.length > 1 ? `${group.length.toLocaleString()} ${point.status} cells · click to expand` : `${point.status} cell · click to inspect`;
        marker.bindTooltip(tooltip).on('click', () => {
          if (group.length > 1) {
            latest.current.onGroup();
            map.fitBounds(group.map(p => [p.latitude, p.longitude]), { padding: [70, 70], maxZoom: 12, animate: !reduced });
          } else latest.current.onInspect(point);
        }).addTo(layers[point.status]);
      }
    }
    map.on('click', e => {
      if (latest.current.placing) {
        const point = e.latlng.wrap();
        latest.current.onPlace(point.lat, point.lng);
      }
    });
    map.on('moveend', () => { draw(); const center = map.getCenter().wrap(); latest.current.onView({ longitude: center.lng, latitude: center.lat, zoom: map.getZoom() }); });
    state.current = { map, tiles, draw, setBasemap(enabled) {
      clearTimeout(deadline); clearTimeout(retryTimer); retryTimer = null;
      healthy = false;
      if (enabled) startTiles(); else { tiles.remove(); report('offline'); }
      draw();
    } };
    mapApi.current = {
      fit(points) {
        const frame = latest.current.frame;
        if (frame?.local && frame.perimeters.features.length) {
          map.fitBounds(L.geoJSON(frame.perimeters).getBounds(), { padding: [100, 100], maxZoom: 15, animate: !reduced });
        } else if (points.length) map.fitBounds(points.map(p => [p.latitude, p.longitude]), { padding: [85, 85], maxZoom: 12, animate: !reduced });
      },
      locate(lat, lon) { map.setView([lat, lon], latest.current.region || latest.current.frame?.local ? 14 : 11, { animate: !reduced }); },
      fitRegion([west, south, east, north]) { map.fitBounds([[south, west], [north, east]], { padding: [40, 40], maxZoom: 12, animate: !reduced }); },
      bounds() { return map.getBounds(); },
    };
    const resize = new ResizeObserver(() => map.invalidateSize());
    resize.observe(container.current);
    return () => { disposed = true; controller.abort(); clearTimeout(deadline); clearTimeout(retryTimer); resize.disconnect(); map.remove(); state.current = null; mapApi.current = null; };
  }, [mapApi]);
  useEffect(() => { state.current?.draw(); }, [region, regions, referenceLayers, frame, selectedCell, visibility]);
  useEffect(() => {
    if (!state.current) return;
    state.current.setBasemap(basemap);
  }, [basemap]);
  // Leaflet owns its container classes. Updating React's className after a
  // placement toggle erased leaflet-container and disabled map clipping/layout.
  useEffect(() => { container.current?.classList.toggle('placing-map', placing); }, [placing]);
  return <div id="map" ref={container} role="region" data-simulation={frame ? frame.local ? 'fuel-patches' : 'reference-grid' : 'none'}
    aria-label="Fire map. Use arrow keys to pan and plus or minus to zoom. Use the scenario cell list to inspect cells."
    aria-describedby="map-alternative" />;
}
