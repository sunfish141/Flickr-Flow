import { useEffect, useState } from 'react';
import { api } from './api';

export function Details({ rows, id }) {
  return <dl id={id}>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}
const percent = value => value == null ? 'Not available' : `${Math.round(value * 100)}%`;
const date = value => new Date(value).toLocaleDateString('en', { month: 'short', year: 'numeric', timeZone: 'UTC' });
export default function Inspector({ point, frame, onClose, headingRef }) {
  const [vegetation, setVegetation] = useState({ status: 'loading' });
  useEffect(() => {
    const controller = new AbortController();
    setVegetation({ status: 'loading' });
    api('/api/vegetation', { cell_id: point.cell_id, origin_at: frame.origin_at, simulation_at: frame.valid_at }, controller.signal)
      .then(setVegetation).catch(error => { if (error.name !== 'AbortError') setVegetation({ status: 'error' }); });
    return () => controller.abort();
  }, [point.cell_id, frame.origin_at, frame.valid_at]);
  const rows = [['Source', point.source], ['Simulated time', `+${frame.elapsed_hours} hours`], ['Cell area', '1 km²']];
  if (frame.local) rows.push(['Active area', `${(point.active_area_m2 / 10000).toFixed(2)} ha`], ['Burned area', `${(point.burned_area_m2 / 10000).toFixed(2)} ha`]);
  else if (point.intensity != null) rows.push(['Scenario intensity', percent(point.intensity)]);
  if (point.fuel_remaining != null) rows.push(['Simulated fuel remaining', percent(point.fuel_remaining)]);
  if (point.burn_duration_hours != null) rows.push(['Assigned burn duration', `${point.burn_duration_hours.toFixed(1)} h`], ['Fuel estimate basis', point.fuel_basis]);
  if (point.vegetation_fraction != null) rows.push(['Vegetated fraction used for fuel', percent(point.vegetation_fraction)]);
  if (point.ignition_probability != null) rows.push(['Last-step spread probability', `${(point.ignition_probability * 100).toFixed(1)}%`]);
  if (point.observation_age_hours != null) rows.push(['Observation age', `${point.observation_age_hours.toFixed(1)} h`]);
  if (point.detection_count != null) rows.push(['FIRMS detections', point.detection_count]);
  if (point.bright_ti4_max != null) rows.push(['Maximum brightness', `${point.bright_ti4_max.toFixed(1)} K`]);
  if (point.landscape) {
    const land = point.landscape;
    rows.push(['Local mapped coverage', percent(land.landscape_valid_fraction)]);
    if (land.landscape_road_length_m != null) rows.push(['Mapped road length', `${Math.round(land.landscape_road_length_m)} m`]);
    rows.push(['Road length with recorded width', percent(land.landscape_road_known_width_fraction)]);
  }
  const data = vegetation, coverRows = [];
  let description = 'Loading vegetation…';
  if (data.status === 'error') description = 'Vegetation could not be loaded. Close and select the cell to retry.';
  else if (data.status === 'unavailable') description = 'No vegetation measurements are available for this cell at the scenario start.';
  else if (data.status === 'available') {
    description = data.density_fraction == null ? 'Mapped vegetation is the share of this cell classified as vegetated land. It is not canopy density; urban land can still contain trees and gardens.' : 'Density is the share of observed area covered by trees or other vegetation.';
    coverRows.push(['Predominant land cover', data.land_cover ?? 'Not available']);
    if (data.mapped_vegetation_fraction != null) coverRows.push(['Mapped vegetated land', percent(data.mapped_vegetation_fraction)]);
    if (data.density_fraction != null) coverRows.push(['Vegetation density', percent(data.density_fraction)], ['Tree cover', percent(data.tree_fraction)], ['Other vegetation', percent(data.other_vegetation_fraction)], ['Nonvegetated cover', percent(data.nonvegetated_fraction)]);
    else {
      coverRows.push(['Canopy density', data.cover_valid_fraction > 0 ? 'Insufficient usable coverage' : 'No usable estimate']);
      if (data.cover_valid_fraction > 0) coverRows.push(['Usable cover area', percent(data.cover_valid_fraction)]);
    }
    if (data.ndvi != null) coverRows.push(['Greenness (NDVI)', data.ndvi.toFixed(2)]);
    if (data.cover_caution_fraction > 0) {
      coverRows.push(['Cover with quality caution', percent(data.cover_caution_fraction)]);
      description += ' Some annual estimates use flagged input periods and have lower confidence.';
    }
    if (data.retrospective_context) description += ' Retrospective static context: these map revisions were retrieved after the scenario start.';
    for (const observation of data.observations) coverRows.push([`${observation.label} observed`, `${date(observation.start)} – ${date(observation.end)}`], [`${observation.label} coverage`, percent(observation.valid_fraction)]);
  }
  const fraction = data.status === 'available' ? data.density_fraction ?? data.mapped_vegetation_fraction : null;
  return <section id="inspector" className="inspector" aria-labelledby="point-title">
    <div className="label-row"><span className="eyebrow">CELL INSPECTOR</span><button id="close-inspector" className="icon-button" onClick={onClose} aria-label="Close cell inspector">×</button></div>
    <h2 id="point-title" tabIndex={-1} ref={headingRef}>{point.status === 'active' ? point.new_ignition ? 'New ignition' : 'Active fire' : point.status === 'burned' ? 'Burned cell' : 'Spread candidate'}</h2>
    <p id="point-location" className="point-location">{point.latitude.toFixed(5)}°, {point.longitude.toFixed(5)}°</p>
    <Details rows={rows} id="point-details" />
    <section className="vegetation-details" aria-labelledby="vegetation-title">
      <h3 id="vegetation-title">Vegetation</h3><p id="vegetation-status" role="status">{description}</p>
      {fraction != null && <meter id="vegetation-density" min="0" max="1" value={fraction} aria-label={data.density_fraction == null ? 'Mapped vegetated land fraction' : 'Vegetation cover fraction'} />}
      <Details rows={coverRows} id="vegetation-details" />
      <p className="vegetation-note">Reference vegetation at scenario start. The 1 km model uses supported cover estimates to assign a fuel duration; the measurements below remain unchanged as simulated fuel is consumed. Fuel amount and burning time are scenario estimates, not measured fuel mass.</p>
    </section><code id="point-id">{point.cell_id}</code>
  </section>;
}
