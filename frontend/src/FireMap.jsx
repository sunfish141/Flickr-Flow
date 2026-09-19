import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

export default function FireMap({ frame, selectedCell, visibility, placing, basemap, mapApi, onPlace, onInspect, onGroup, onError }) {
  const container = useRef(null);
  const state = useRef(null);
  const latest = useRef(null);
  latest.current = { frame, selectedCell, visibility, placing, onPlace, onInspect, onGroup, onError };
  useEffect(() => {
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const map = L.map(container.current, { zoomControl: false, preferCanvas: true, minZoom: 2, maxZoom: 16,
      zoomAnimation: !reduced, fadeAnimation: !reduced, markerZoomAnimation: !reduced,
      maxBounds: [[22, -180], [85, -48]], maxBoundsViscosity: 0.8 }).setView([53.02, -117.31], 10);
    L.control.zoom({ position: 'topright' }).addTo(map);
    const layers = { coverage: L.layerGroup().addTo(map), roads: L.layerGroup().addTo(map), burned: L.layerGroup().addTo(map), active: L.layerGroup().addTo(map), candidate: L.layerGroup().addTo(map), historical: L.layerGroup().addTo(map) };
    const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    });
    let tileErrorShown = false;
    tiles.on('tileerror', () => {
      if (!tileErrorShown) { tileErrorShown = true; latest.current.onError('Map tiles are unavailable. Coordinate placement, the cell list, and simulation still work.'); }
    });
    function draw() {
      Object.values(layers).forEach(layer => layer.clearLayers());
      const { frame, visibility, selectedCell } = latest.current;
      if (!frame) return;
      if (frame.local) {
        if (frame.coverage) L.geoJSON(frame.coverage, { interactive: false, style: { color: '#537775', weight: 1.5, dashArray: '6 5', fill: false } }).addTo(layers.coverage);
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
      }
      const view = map.getBounds().pad(.1);
      const visible = [...frame.points, ...(frame.historical?.points || [])].filter(p => view.contains([p.latitude, p.longitude]));
      const groups = new Map();
      for (const point of visible) {
        if (!visibility[point.status] || (frame.local && point.status !== 'historical')) continue;
        const pixel = map.project([point.latitude, point.longitude]);
        const pointKey = `${point.status}:${point.cell_id}`;
        const key = visible.length > 1500 && map.getZoom() < 11 && pointKey !== selectedCell ?
          `${point.status}:${Math.floor(pixel.x / 32)}:${Math.floor(pixel.y / 32)}` : pointKey;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(point);
      }
      for (const group of groups.values()) {
        const point = group[0];
        const active = point.status === 'active', candidate = point.status === 'candidate', historical = point.status === 'historical';
        const color = historical ? '#6536b3' : active ? '#b83e21' : candidate ? '#806229' : '#5d5a52';
        const marker = L.circleMarker([point.latitude, point.longitude], {
          radius: group.length > 1 ? Math.min(20, 7 + Math.log2(group.length)) + (historical ? 4 : 0) : historical ? 12 : active ? 5 + point.intensity * 4 : 5,
          color, weight: selectedCell === `${point.status}:${point.cell_id}` ? 4 : historical ? 2 : 1,
          fillColor: active ? `hsl(${12 + (1 - point.intensity) * 24}, 85%, 55%)` : candidate ? '#efd5a6' : '#767169',
          fillOpacity: historical ? 0 : candidate ? .45 : .88, bubblingMouseEvents: false,
        });
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
    map.on('click', e => { if (latest.current.placing) latest.current.onPlace(e.latlng.lat, e.latlng.lng); });
    map.on('moveend', draw);
    state.current = { map, tiles, draw };
    mapApi.current = {
      fit(points) { if (points.length) map.fitBounds(points.map(p => [p.latitude, p.longitude]), { padding: [85, 85], maxZoom: 12, animate: !reduced }); },
      locate(lat, lon) { map.setView([lat, lon], 11, { animate: !reduced }); },
      fitRegion([west, south, east, north]) { map.fitBounds([[south, west], [north, east]], { padding: [40, 40], maxZoom: 12, animate: !reduced }); },
      bounds() { return map.getBounds(); },
    };
    const resize = new ResizeObserver(() => map.invalidateSize());
    resize.observe(container.current);
    return () => { resize.disconnect(); map.remove(); state.current = null; mapApi.current = null; };
  }, [mapApi]);
  useEffect(() => { state.current?.draw(); }, [frame, selectedCell, visibility]);
  useEffect(() => {
    if (!state.current) return;
    const { map, tiles } = state.current;
    if (basemap) tiles.addTo(map); else tiles.remove();
  }, [basemap]);
  return <div id="map" ref={container} className={placing ? 'placing-map' : ''} role="region"
    aria-label="Fire map. Use arrow keys to pan and plus or minus to zoom. Use the scenario cell list to inspect cells."
    aria-describedby="map-alternative" />;
}
