import { Details } from './Inspector';

export default function HistoricalInspector({ point, historical, onClose, headingRef }) {
  return <section id="inspector" className="inspector" aria-labelledby="point-title">
    <div className="label-row"><span className="eyebrow">OBSERVED FIRMS CELL</span><button id="close-inspector" className="icon-button" onClick={onClose} aria-label="Close cell inspector">×</button></div>
    <h2 id="point-title" tabIndex={-1} ref={headingRef}>Historical FIRMS</h2>
    <p id="point-location" className="point-location">{point.latitude.toFixed(5)}°, {point.longitude.toFixed(5)}°</p>
    <Details id="point-details" rows={[
      ['Source', 'Retained NASA FIRMS'], ['Observation day (UTC)', historical.date],
      ['Detections in this cell', point.detection_count], ['Cell area', '1 km²'],
    ]} />
    <p className="hint">Observed satellite detections, not simulated intensity or a measured fire perimeter. Purple rings can overlap predicted fire cells.</p>
    <code id="point-id">{point.cell_id}</code>
  </section>;
}
