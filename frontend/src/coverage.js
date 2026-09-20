// Geographic preflight only. The server checks projected pack bounds and fuel
// support authoritatively; a rectangular bbox alone must not imply coverage.
function inRing(lon, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [x, y] = ring[i], [px, py] = ring[j];
    const cross = (lon - x) * (py - y) - (lat - y) * (px - x);
    if (Math.abs(cross) < 1e-10 && lon >= Math.min(x, px) && lon <= Math.max(x, px)
      && lat >= Math.min(y, py) && lat <= Math.max(y, py)) return true;
    if ((y > lat) !== (py > lat) && lon < (px - x) * (lat - y) / (py - y) + x) inside = !inside;
  }
  return inside;
}

export function contains(geometry, lon, lat) {
  if (!geometry || !Number.isFinite(lon) || !Number.isFinite(lat)) return false;
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.type === 'MultiPolygon' ? geometry.coordinates : [];
  return polygons.some(rings => inRing(lon, lat, rings[0]) && !rings.slice(1).some(ring => inRing(lon, lat, ring)));
}

export function installedRegionAt(regions, lon, lat) {
  return regions.find(region => contains(region.coverage?.geometry, lon, lat));
}

export const shortRegionName = region => (region?.label || 'Regional scenario').replace(/\s*\([^)]*\)\s*$/, '');

// Installed fuel evidence always wins; never reinterpret an existing run.
export function placementRoute({ region, frame, online, modelReady }) {
  const target = region?.id || 'reference-grid';
  const current = frame && (frame.local ? frame.region_id || frame.state.region : 'reference-grid');
  if (current && current !== target) return { error: 'This location uses different simulation data. Reset before starting here; the current results are unchanged.' };
  if (region) return { endpoint: region.tiled ? 'landscape/seed' : 'map/seed' };
  if (!online) return { error: 'You are offline and regional data is not installed here. Choose an outlined installed region; online exploration resumes when connected.' };
  if (!modelReady) return { error: 'The broader research model is unavailable. Installed regions can still run; Wi-Fi alone does not supply simulation data.' };
  return { endpoint: 'seed' };
}

export function regionForView(regions, bounds) {
  const region = installedRegionAt(regions, (bounds.west + bounds.east) / 2, (bounds.south + bounds.north) / 2);
  if (!region) return null;
  // Region-wide coverage must not turn a province-wide satellite request into
  // a province-sized mesh. Require a local view before using detailed seeds.
  if (region.tiled) return bounds.east - bounds.west <= .5 && bounds.north - bounds.south <= .5 ? region : null;
  const [west, south, east, north] = region.bounds;
  return bounds.east - bounds.west <= 4 * (east - west) && bounds.north - bounds.south <= 4 * (north - south) ? region : null;
}
