import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

const colors = { needleleaf: '#51745c', broadleaf: '#70935b', mixed_forest: '#64816b', shrubland: '#a7b481',
  grassland: '#c3cd97', other_vegetation: '#9aaf88', cropland: '#d8cf96', wetland: '#83aea6',
  urban: '#b08baa', water: '#96c8dd', barren: '#c7bfb3', snow_ice: '#e1eaed', unknown: '#b08baa' };

export default function PlanningMap({ pack, frame, ignitions = [], onIgnition, label }) {
  const host = useRef(), map = useRef(), layers = useRef(), click = useRef(onIgnition);
  click.current = onIgnition;
  useEffect(() => {
    const m = L.map(host.current, { preferCanvas: true, attributionControl: false, minZoom: 8, maxZoom: 18 });
    map.current = m;
    layers.current = L.layerGroup().addTo(m);
    L.control.scale({ imperial: false }).addTo(m);
    m.on('click', event => click.current?.({ longitude: event.latlng.lng, latitude: event.latlng.lat }));
    const observer = new ResizeObserver(() => m.invalidateSize());
    observer.observe(host.current);
    return () => { observer.disconnect(); m.remove(); };
  }, []);
  useEffect(() => {
    if (!pack) return;
    const [w, s, e, n] = pack.bounds_wgs84;
    map.current.fitBounds([[s, w], [n, e]], { padding: [12, 12] });
    map.current.setMaxBounds([[s - .1, w - .1], [n + .1, e + .1]]);
  }, [pack?.digest]);
  useEffect(() => {
    const group = layers.current;
    group.clearLayers();
    if (!pack) return;
    for (const name of ['cover', 'unknown', 'roads', 'boundary']) {
      if (!pack.layers[name]) continue;
      L.geoJSON(pack.layers[name], {
        style: f => name === 'roads' ? { color: '#515d66', weight: 1, opacity: .7 }
          : name === 'boundary' ? { fill: false, color: '#1e4144', weight: 2, dashArray: '6 5' }
          : { fillColor: colors[f.properties.fuel] || '#b08baa', fillOpacity: .85, weight: 0 },
        onEachFeature(f, layer) {
          if (name !== 'boundary') {
            const node = document.createElement('span');
            node.textContent = name === 'roads'
              ? `Road: ${f.properties.class || 'unknown class'}; width ${f.properties.width_m ?? 'unknown'}; surface ${f.properties.surface ?? 'unknown'}; grade ${f.properties.at_grade == null ? 'unknown' : f.properties.at_grade ? 'at grade' : 'elevated/tunnel'}`
              : `${f.properties.fuel.replaceAll('_', ' ')}${['unknown', 'urban'].includes(f.properties.fuel) ? ' — unsupported, NOT safe' : ''}`;
            layer.bindTooltip(node, { sticky: true });
          }
        },
      }).addTo(group);
    }
    if (frame) L.geoJSON(frame.result.perimeters, {
      style: f => ({ color: f.properties.status === 'active' ? '#f15b26' : '#632f40', fillOpacity: .65, weight: 1.5 }),
    }).addTo(group);
    for (const ignition of ignitions) L.circleMarker([ignition.latitude, ignition.longitude], {
      radius: 6, color: '#fff', weight: 2, fillColor: '#b7370a', fillOpacity: 1,
    }).addTo(group);
  }, [pack, frame, ignitions]);
  return <div className="map-wrap"><div ref={host} className="planning-map" aria-label={label} />
    <div className="map-caption">North up · outside dashed boundary unsupported<br />
      {pack && <>W {pack.bounds_wgs84[0].toFixed(4)} / S {pack.bounds_wgs84[1].toFixed(4)} / E {pack.bounds_wgs84[2].toFixed(4)} / N {pack.bounds_wgs84[3].toFixed(4)} · {pack.coverage.area_km2.toFixed(1)} km²</>}</div></div>;
}
