"""Experimental 1 km ML admission gates over the native polygon travel graph.

Probabilities never become velocities or bypass geometry. A 12-hour window
uses frozen fire proxies and weather; accepted edge travel keeps the wind at
departure. This coupling has not been calibrated as a perimeter forecast.
"""
from datetime import timedelta
import heapq
import math

import numpy as np
import pandas as pd

from wildfire_data.core.grid import cell_from_id, cells_in_square_radius
from wildfire_data.model.features.fire_state_features import build_firms_fire_state_features
from wildfire_data.model.features.directional_fire_features import detection_index, directional_features
from wildfire_data.model.features.fuel_barrier_features import projected_wind
from wildfire_data.model.features.schema import FRONTIER_BASELINE_COLUMNS
from wildfire_data.model.incident_transition import stable_fraction


VERSION = 'weather-ml-polygon-gates/v1'


def fire_records(active, cutoff, calibration):
    """Explicit synthetic observations; never presented as satellite readings."""
    records = []
    for cell_id, intensity in sorted(active.items()):
        latitude, longitude = cell_from_id(cell_id).center_wgs84
        count, platforms = calibration.counts(intensity)
        for n in range(count):
            records.append({'detection_id': f'{cell_id}:{n}', 'cell_id': cell_id, 'latitude': latitude, 'longitude': longitude,
                            'acquired_at': cutoff-timedelta(hours=7.5), 'bright_ti4': 305.+62.*intensity,
                            'platform': f'synthetic-{n%platforms}'})
    return records


def feature_rows(cell_ids, active, cutoff, weather, terrain, calibration):
    records = fire_records(active, cutoff, calibration)
    indexed = detection_index(records)
    raw_index = {}
    for record in records:
        cell = cell_from_id(record['cell_id'])
        raw_index.setdefault((cell.x_index,cell.y_index),[]).append(record)
    rows = []
    for cell_id in cell_ids:
        cell = cell_from_id(cell_id)
        local = [record for dx in range(-1,2) for dy in range(-1,2)
                 for record in raw_index.get((cell.x_index+dx,cell.y_index+dy),())]
        # Feature functions reproduce the retained observed-row contract.
        row = build_firms_fire_state_features(local, cell_id=cell_id, cutoff_at=cutoff,
                    lookback=timedelta(hours=24), availability_lag=timedelta(hours=3))
        row.update({name: float('nan') for name in FRONTIER_BASELINE_COLUMNS if name.startswith('terrain_')})
        row.update(terrain(cell_id))
        row.update(weather)
        directional, _ = directional_features(cell_id, cutoff, indexed,
                    wind_u=weather['weather_wind_u_10m'], wind_v=weather['weather_wind_v_10m'])
        row.update(directional)
        rows.append(row)
    return pd.DataFrame(rows)


class HybridSearch:
    def __init__(self, model, seeds, origin, snapshot, estimator, calibration, terrain, identity):
        if model.policy.max_spotting_distance_m or model.policy.spotting_distance_per_wind_m_s:
            raise ValueError('Weather ML polygon mode currently requires spotting to be disabled')
        self.model, self.origin, self.snapshot = model, origin, snapshot
        self.estimator, self.calibration, self.terrain, self.identity = estimator, calibration, terrain, identity
        self.times = {i: 0. for i in seeds}
        self.queue = [(0., False, i) for i in seeds]
        heapq.heapify(self.queue)
        self.settled = set()
        self.admissions = {model.patches[i].cell_id: (0., None) for i in seeds}
        self.epoch = -1
        self.probabilities = {}
        self.accepted = set()
        self.horizon = 0.

    def begin_window(self, epoch):
        minute = epoch*720
        cutoff = self.origin+timedelta(minutes=minute)
        weather, _ = self.snapshot.features(cutoff)
        active = {}
        for i, arrival in self.times.items():
            remaining = 1-(minute-arrival)/self.model.residence[i]
            if arrival <= minute and remaining > 0:
                cell_id = self.model.patches[i].cell_id
                active[cell_id] = max(active.get(cell_id, 0.), remaining)
        candidates = sorted({cell.cell_id for key in active for cell in cells_in_square_radius(cell_from_id(key),radius_cells=2)}
                            - self.admissions.keys() - active.keys())
        probabilities = {}
        if candidates:
            rows = feature_rows(candidates, active, cutoff, weather, self.terrain, self.calibration)
            values = np.asarray(self.estimator.predict_frame(rows), dtype=float)
            if values.shape != (len(candidates),) or not np.isfinite(values).all() or ((values < 0)|(values > 1)).any():
                raise ValueError('Weather ML produced invalid admission probabilities')
            probabilities = dict(zip(candidates, map(float, values)))
        accepted = {cell_id for cell_id,p in probabilities.items() if p >= self.estimator.threshold
                    and stable_fraction(f'{VERSION}:{self.identity}:{epoch}:{cell_id}') < p}
        location = self.snapshot.document['location']
        wind = projected_wind(self.model.sampler.to_grid, (location['latitude'], location['longitude']),
                              weather['weather_wind_u_10m'], weather['weather_wind_v_10m'])
        # Install atomically after the provider/estimator has succeeded.
        self.probabilities, self.accepted, self.wind, self.epoch = probabilities, accepted, wind, epoch

    def advance(self, until):
        if not math.isfinite(until) or until < 0:
            raise ValueError('Hybrid playback time must be finite and nonnegative')
        while self.queue and self.queue[0][0] <= until:
            time, retry, i = self.queue[0]
            # At a displayed window boundary, admit arriving patches without
            # expanding their outgoing edges into the next weather window yet.
            if time == until and time > 0 and time % 720 == 0:
                break
            epoch = int(time//720)
            if epoch != self.epoch:
                self.begin_window(epoch)
            heapq.heappop(self.queue)
            if not retry and (time != self.times.get(i) or i in self.settled):
                continue
            if retry and time > self.times[i]+self.model.residence[i]:
                continue
            self.settled.add(i)
            source = self.model.patches[i]
            self.admissions.setdefault(source.cell_id, (time, self.probabilities.get(source.cell_id)))
            blocked = False
            for j, first, second in self.model.adjacency[i]:
                if j in self.settled:
                    continue
                target = self.model.patches[j]
                if target.cell_id not in self.admissions and target.cell_id not in self.accepted:
                    blocked = True
                    continue
                source_rate = self.model.policy.rates_m_min[source.fuel]
                target_rate = self.model.policy.rates_m_min[target.fuel]
                if min(source_rate, target_rate) <= 0:
                    continue
                ax,ay = self.model.center_xy[i]
                bx,by = self.model.center_xy[j]
                dx,dy = bx-ax,by-ay
                along = (self.wind[0]*dx+self.wind[1]*dy)/max(math.hypot(dx,dy),1e-9)
                response = math.exp(max(-3.,min(3.,self.model.policy.wind_coefficient*along)))
                departure = first/(source_rate*response)
                if time-self.times[i]+departure > self.model.residence[i]:
                    continue
                arrival = time+departure+second/(target_rate*response)
                if arrival < self.times.get(j,math.inf):
                    self.times[j] = arrival
                    heapq.heappush(self.queue,(arrival,False,j))
            boundary = (epoch+1)*720
            if blocked and boundary < self.times[i]+self.model.residence[i]:
                heapq.heappush(self.queue,(boundary,True,i))
        self.horizon = max(self.horizon,until)
        return self.times

    def frame(self, until):
        self.advance(until)
        result = self.model.frame_from_arrivals(self.times,until)
        result['future_arrivals'] |= any(time > until for time,_,_ in self.queue)
        # Display the weather used for the interval that produced this frame.
        used = max(0,math.ceil(until/720)-1)*720
        _, weather = self.snapshot.features(self.origin+timedelta(minutes=used))
        if (weather['mode'] == 'forecast' and not weather['extended']
                and self.origin+timedelta(minutes=until) > self.snapshot.end):
            weather = {**weather, 'extended': True, 'time_basis': 'window weather held beyond forecast coverage'}
        result['assumptions'] = {**result['assumptions'], 'weather_mode': weather['time_basis'],
            'hybrid': {'kind': VERSION, 'weather': weather, 'model_sha256': self.identity,
                       'threshold': self.estimator.threshold, 'probability_resolution_m': 1000,
                       'feature_basis': 'simulated active patches rendered as synthetic fire observations',
                       'terrain_basis': 'configured terrain provider; unavailable values remain NaN',
                       'coupling': 'one reproducible admission draw per new 1 km cell per 12-hour window; native geometry still constrains travel',
                       'validated_perimeter_forecast': False}}
        return result
