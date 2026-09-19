import { useState } from 'react';

const PAGE_SIZE = 25;
export default function CellList({ points, onInspect }) {
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const filtered = points.filter(p => `${p.cell_id} ${p.status}`.includes(query.trim().toLowerCase()));
  const last = Math.max(0, Math.ceil(filtered.length / PAGE_SIZE) - 1);
  const current = Math.min(page, last);
  return <details className="cell-list" id="cell-list">
    <summary>Scenario cell list ({points.length.toLocaleString()})</summary>
    <p id="map-alternative">Inspect every cell using this list, including cells outside the map view. Labels identify fire state without relying on color.</p>
    <label htmlFor="cell-search">Filter by cell ID or state</label>
    <input id="cell-search" type="search" value={query} onChange={e => { setQuery(e.target.value); setPage(0); }} />
    <p role="status">{filtered.length.toLocaleString()} cells. Page {current + 1} of {last + 1}.</p>
    <ul>{filtered.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE).map(point => <li key={`${point.status}:${point.cell_id}`}>
      <button type="button" onClick={e => onInspect(point, e.currentTarget)}>
        <strong>{point.status} · {point.latitude.toFixed(4)}°, {point.longitude.toFixed(4)}°</strong>
        <span>{point.cell_id}</span>
      </button>
    </li>)}</ul>
    <div className="pagination"><button disabled={current === 0} onClick={() => setPage(current - 1)}>Previous cells</button><button disabled={current === last} onClick={() => setPage(current + 1)}>Next cells</button></div>
  </details>;
}
