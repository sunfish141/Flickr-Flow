"""Pinned offline road snapshots: resumable collection and bounded local queries.

Collection is the only network path. Runtime opens verified local GeoParquet.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import shutil

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import shapely
from shapely.geometry import box, mapping
from shapely.ops import unary_union

from wildfire_data.core.hashing import sha256_file
from wildfire_data.providers.landscape.collect import checked_bounds, save_json
from wildfire_data.providers.storage.storage_budget import load_storage_budget, require_admission

ARCHIVE_KIND = 'offline-overture-roads/v1'
COLUMNS = ('id', 'geometry', 'bbox', 'version', 'subtype', 'class', 'subclass',
           'width_rules', 'road_surface', 'level_rules', 'road_flags', 'sources')
BOUNDS = (-179., 24., -50., 84.)


def bbox_filter(bounds):
    w, s, e, n = bounds
    return ((pc.field('bbox','xmin') <= e) & (pc.field('bbox','xmax') >= w)
            & (pc.field('bbox','ymin') <= n) & (pc.field('bbox','ymax') >= s))




def admit(root, amount):
    require_admission(load_storage_budget(), root, category='static_cell_features', requested_bytes=amount)
    if shutil.disk_usage(root).free < amount + 100_000_000:
        raise ValueError('Insufficient free disk for road archive staging')






@lru_cache(maxsize=64)
def verify_partition(path, size, modified_ns, checksum):
    if sha256_file(Path(path)) != checksum:
        raise ValueError('Road archive partition checksum mismatch')


class RoadArchive:
    def __init__(self, manifest_path, *, expected_sha256=None):
        self.path=Path(manifest_path).resolve()
        self.sha256=sha256_file(self.path)
        if expected_sha256 and self.sha256!=expected_sha256:
            raise ValueError('Road archive manifest checksum mismatch')
        self.manifest=json.loads(self.path.read_text())
        m=self.manifest
        if m.get('kind')!=ARCHIVE_KIND or m.get('status')!='complete' or not m.get('partitions'):
            raise ValueError('Requires a complete offline road archive')
        if sum(p['rows'] for p in m['partitions'])!=m['row_count']:
            raise ValueError('Road archive row inventory mismatch')
        asset=m['coverage']
        path=(self.path.parent/asset['path']).resolve()
        if path.parent!=self.path.parent or sha256_file(path)!=asset['sha256']:
            raise ValueError('Road archive coverage checksum/path mismatch')
        self.coverage=shapely.from_wkb(path.read_bytes())
        shapely.prepare(self.coverage)
        for p in m['partitions']:
            if p['path'] and (self.path.parent/p['path']).resolve().parent!=self.path.parent:
                raise ValueError('Road archive partition path escapes archive')

    def features(self, bounds):
        bounds=checked_bounds(bounds)
        if not box(*self.manifest['bounds']).covers(box(*bounds)) or not self.coverage.covers(box(*bounds)):
            raise ValueError('Requested area is outside offline road archive coverage')
        selected=[]
        for p in self.manifest['partitions']:
            if not p['rows'] or not box(*p['bounds']).intersects(box(*bounds)):
                continue
            path=self.path.parent/p['path']
            stat=path.stat()
            verify_partition(str(path),stat.st_size,stat.st_mtime_ns,p['sha256'])
            selected.append(str(path))
        if not selected:
            return
        dataset=ds.dataset(selected,format='parquet')
        for batch in dataset.to_batches(filter=bbox_filter(bounds),batch_size=4096):
            for row in batch.to_pylist():
                geometry=shapely.from_wkb(row.pop('geometry'))
                if geometry.intersects(box(*bounds)):
                    yield {'type':'Feature','geometry':mapping(geometry),'properties':row}

    def extract(self, bounds, directory, data_root, *, max_bytes=100_000_000):
        """Materialize a bounded local adapter for the existing bundle builder."""
        bounds=checked_bounds(bounds)
        directory=Path(directory)
        path=directory/'roads-manifest.json'
        if path.exists():
            m=json.loads(path.read_text())
            if m.get('archive_sha256')!=self.sha256 or m['bounds']!=list(bounds) or sha256_file(directory/m['path'])!=m['sha256']:
                raise ValueError('Existing local road extract identity mismatch')
            return path
        admit(Path(data_root),max_bytes)
        directory.mkdir(parents=True,exist_ok=True)
        temporary=directory/'roads.geojsonl.partial'
        count=size=0
        with temporary.open('wb') as stream:
            for feature in self.features(bounds):
                line=(json.dumps(feature,default=str,allow_nan=False)+'\n').encode()
                if size+len(line)>max_bytes:
                    raise ValueError('Local road extract exceeds tile budget')
                stream.write(line); size+=len(line); count+=1
        target=directory/'roads.geojsonl'
        temporary.replace(target)
        m={k:self.manifest[k] for k in ('release','available_at','retrieved_at','observation_end','observation_basis',
                                       'historical_geometry','source','license','attribution')}
        m.update(kind='overture-road-extract/v1',status='complete',bounds=list(bounds),path=target.name,
                 sha256=sha256_file(target),feature_count=count,bytes=size,archive_sha256=self.sha256,
                 availability_basis='offline pinned archive; tile extraction does not change capture time')
        save_json(path,m,data_root)
        return path



