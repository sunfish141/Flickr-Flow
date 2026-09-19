"""Static terrain lookup for retained candidate cells, with explicit missingness."""
import pandas as pd

from wildfire_data.model.features.schema import FRONTIER_BASELINE_COLUMNS


class CSVTerrainProvider:
    def __init__(self, path):
        fields = [c for c in FRONTIER_BASELINE_COLUMNS if c.startswith('terrain_')]
        frame = pd.read_csv(path, usecols=['cell_id', *fields])
        if frame.cell_id.isna().any() or not frame.cell_id.is_unique:
            raise ValueError('Terrain lookup requires unique cell IDs')
        self.rows = frame.set_index('cell_id').to_dict(orient='index')

    def __call__(self, cell_id):
        row = self.rows.get(cell_id)
        if row is None:
            return {'terrain_valid': False, 'terrain_aspect_defined': False,
                    'terrain_coverage_status': 'outside-csv-coverage'}
        return {**row, 'terrain_coverage_status': 'sampled' if row['terrain_valid'] == 1 else 'missing-source'}
