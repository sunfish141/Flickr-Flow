"""Self-contained, script-free research reports and WGS84 GeoJSON."""
from datetime import datetime, timedelta
from html import escape
import json
import math

from wildfire_data.planning.contracts import LIMITATIONS

COLORS = {'needleleaf': '#51745c', 'broadleaf': '#70935b', 'mixed_forest': '#64816b',
          'shrubland': '#a7b481', 'grassland': '#c3cd97', 'other_vegetation': '#9aaf88',
          'cropland': '#d8cf96', 'wetland': '#83aea6', 'urban': '#b08baa',
          'water': '#96c8dd', 'barren': '#c7bfb3', 'snow_ice': '#e1eaed', 'unknown': '#b08baa'}


def metrics(frame):
    return {status: sum(f['properties']['area_m2'] for f in frame['perimeters']['features']
                       if f['properties']['status'] == status)/1e6 for status in ('active', 'burned')}


def geojson(document):
    features = []
    for frame in document['frames']:
        for feature in frame['result']['perimeters']['features']:
            features.append({**feature, 'properties': {**feature['properties'], 'elapsed_hours': frame['hour'],
                'scenario_id': document['id'], 'origin_time': document['definition']['origin_time'],
                'scenario_time': (datetime.fromisoformat(document['definition']['origin_time'].replace('Z', '+00:00')) + timedelta(hours=frame['hour'])).isoformat(),
                'boundary_reached': frame['result']['boundary_reached']}})
    return {'type': 'FeatureCollection', 'features': features, 'metadata': {
        k: v for k, v in document.items() if k != 'frames'}}


def svg_map(snapshot, frame=None):
    w, s, e, n = snapshot['bounds_wgs84']
    scale_x = math.cos(math.radians((s+n)/2))
    width, height = (e-w)*scale_x, n-s
    drawing_height = height/width*600
    def position(p):
        return f'{(p[0]-w)*scale_x/width*600:.3f},{(n-p[1])/width*600:.3f}'
    def path(g):
        coordinates = g['coordinates']
        if g['type'] == 'Polygon':
            coordinates = [coordinates]
        if g['type'] in ('Polygon', 'MultiPolygon'):
            return ' '.join('M'+' L'.join(position(p) for p in ring)+' Z' for polygon in coordinates for ring in polygon)
        lines = [coordinates] if g['type'] == 'LineString' else coordinates
        return ' '.join('M'+' L'.join(position(p) for p in line) for line in lines)
    paths = []
    for name in ('cover', 'unknown', 'roads'):
        for f in snapshot['layers'].get(name, {}).get('features', []):
            color = COLORS.get(f['properties'].get('fuel'), '#b08baa')
            attrs = 'fill="none" stroke="#515d66" stroke-width="0.8"' if name == 'roads' else f'fill="{color}"'
            paths.append(f'<path d="{path(f["geometry"])}" {attrs} fill-rule="evenodd"/>')
    if frame:
        for f in frame['perimeters']['features']:
            color = '#f36b35' if f['properties']['status'] == 'active' else '#633741'
            paths.append(f'<path d="{path(f["geometry"])}" fill="{color}" fill-opacity="0.8" fill-rule="evenodd"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 {drawing_height+40:.3f}" role="img" aria-label="Offline scenario map"><rect width="600" height="{drawing_height:.3f}" fill="#b08baa"/>' + ''.join(paths) + f'<text x="8" y="{drawing_height+20:.3f}" font-size="12">W {w:.4f} / S {s:.4f} / E {e:.4f} / N {n:.4f} — north up</text></svg>'


def html_report(document):
    last = document['frames'][-1] if document['frames'] else None
    rows = []
    for item in document['frames']:
        m = metrics(item['result'])
        rows.append(f'<tr><td>{item["hour"]}</td><td>{m["active"]:.4f}</td><td>{m["burned"]:.4f}</td><td>{item["result"]["boundary_reached"]}</td></tr>')
    title = escape(document['name'])
    metadata = {k: v for k, v in document.items() if k != 'frames'}
    metadata['pack_snapshot'] = {k: v for k, v in document['pack_snapshot'].items() if k != 'layers'}
    assumptions = escape(json.dumps(metadata, ensure_ascii=False, indent=2))
    return f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} — research scenario</title>
<style>body{{font:16px system-ui;max-width:1000px;margin:2rem auto;padding:1rem;color:#243538;background:#f7f6ef}}
svg{{max-width:650px;width:100%}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}td,th{{padding:.5rem;border-bottom:1px solid #aaa;text-align:left}}
.warning{{padding:1rem;background:#ffe5c5}}h1{{font-size:2rem}}</style>
<h1>{title}</h1><p>Offline planning / unsigned internal research build</p>
<div class="warning"><strong>Research only — not an operational forecast.</strong><ul>{''.join('<li>'+escape(v)+'</li>' for v in LIMITATIONS)}</ul>
<p>Imported/shared results are not independently verified. This report is not digitally signed.</p></div>
<p>Origin: {escape(document['definition']['origin_time'])}. Exported: {escape(document['exported_at'])}.
Last committed hour: {last['hour'] if last else 'not run'}. Coverage: {escape(document['pack_snapshot']['label'])}.</p>
{svg_map(document['pack_snapshot'], last['result'] if last else None)}
<p>Orange: modeled active. Plum: modeled burned. Mauve: urban/unknown, unsupported—not safe.
Blue: mapped water. Roads are reference data, not guaranteed firebreaks.</p>
<table><caption>Modeled area at each saved checkpoint</caption><thead><tr><th>Elapsed hour</th><th>Active km²</th><th>Burned km²</th><th>Boundary reached</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>Exact assumptions, pack coverage and provenance</h2><pre>{assumptions}</pre></html>'''
