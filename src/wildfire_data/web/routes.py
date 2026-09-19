"""HTTP contract: stateless scenario requests over injected runtime resources."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from time import monotonic
import logging

from fastapi import HTTPException
from wildfire_data.core.grid import cell_from_wgs84
from wildfire_data.web.schemas import BoundsInput, SeedInput, StepInput, VegetationInput, HistoricalFirmsInput
from wildfire_data.web.serialization import state_response
from wildfire_data.web.live_firms import DEFAULT_BOUNDS, fetch_current_firms, aggregate_current_firms, LiveFirmsError
from wildfire_data.web.historical_firms import HistoricalFirmsError, FIRST_DAY, LAST_DAY, day_cutoff
from wildfire_data.web.vegetation import vegetation_summary


def register_routes(app, runtime):
    @app.get("/api/config")
    def config():
        return {"model_ready": runtime.model_error is None, "model_error": runtime.model_error,
            "local_spread": runtime.local_scenarios.configuration(),
            "model_name": ('Frontier CSV model' if runtime.public_model else f"Incident model · {runtime.settings.pass_name.replace('_', ' ')}"), "research_preview": True,
            "firms_configured": bool(runtime.settings.firms_key or runtime.firms_loader), "step_hours": 12,
            "vegetation_available": runtime.vegetation is not None,
            "data_preparation": runtime.data_preparation,
            "max_steps": None, "default_speed_seconds": 3, "max_seed_cells": 500,
            "transition": runtime.model.transition_contract() if runtime.model_error is None else None,
            "firms_bounds": list(DEFAULT_BOUNDS),
            "historical_firms": {"available": bool(runtime.historical and runtime.historical.available),
                "min_date": FIRST_DAY.isoformat(), "max_date": LAST_DAY.isoformat(), "step_hours": 24},
            "request_limits": {"bytes": 8 * 1024 * 1024, "active_cells": 10000, "burned_cells": 100000}}

    @app.post("/api/seed")
    def seed(body: SeedInput):
        current = runtime.ready_model()
        ignitions = {}
        for point in body.ignitions:
            cell_id = cell_from_wgs84(latitude=point.latitude, longitude=point.longitude).cell_id
            ignitions[cell_id] = max(ignitions.get(cell_id, 0), point.intensity)
        try:
            state = current.initial_state(ignitions)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return state_response(state, origin_at=datetime.now(timezone.utc))

    @app.post("/api/step")
    def step(body: StepInput):
        current = runtime.ready_model()
        try:
            state = body.state.to_state()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        historical = None
        if body.historical:
            day = body.historical.start_date + timedelta(days=state.step_index // 2 + 1)
            _, historical = historical_day(day, body.historical.bounds)
            historical['start_date'] = body.historical.start_date.isoformat()
        missing = set()
        def terrain(cell_id):
            values = runtime.terrain(cell_id)
            if values.get("terrain_coverage_status") != "sampled":
                missing.add(cell_id)
            return values
        with runtime.available('inference'):
            new_ignitions = 0
            for _ in range(2 if historical is not None else 1):
                result = current.step(state, terrain_provider=terrain, origin_at=body.origin_at)
                state = result.state
                new_ignitions += sum(p.will_ignite for p in result.predictions)
        response = state_response(result.state, origin_at=body.origin_at,
            predictions=result.predictions, terrain_missing=len(missing))
        response['new_ignition_count'] = new_ignitions
        if historical is not None:
            response.update(historical=historical, finished=historical['date'] == LAST_DAY.isoformat())
        return response

    def historical_day(day, bounds):
        if runtime.historical is None:
            raise HTTPException(503, 'Historical FIRMS archive is unavailable on this server.')
        try:
            with runtime.available('historical'):
                return runtime.historical.load(day, (bounds.west, bounds.south, bounds.east, bounds.north))
        except HistoricalFirmsError as exc:
            raise HTTPException(503, str(exc)) from None

    @app.post('/api/firms/historical')
    def historical_firms(body: HistoricalFirmsInput, landscape_mode: bool = False):
        current = None if landscape_mode else runtime.ready_model()
        rows, historical = historical_day(body.date, body.bounds)
        origin = day_cutoff(body.date)
        bounds = tuple(historical['bounds'][key] for key in ('west', 'south', 'east', 'north'))
        state, metadata = aggregate_current_firms(rows, bounds, now=origin)
        cells = tuple(replace(c, intensity=max(.1, c.intensity)) for c in state.active_cells
                      if current is None or current.landscape.allows_cell(c.cell_id))
        if len(cells) > 10000:
            raise HTTPException(422, 'Too many starting cells. Load a smaller visible map area.')
        metadata.update(water_cells_excluded=len(state.active_cells) - len(cells), source='Historical NASA FIRMS')
        response = state_response(replace(state, active_cells=cells), origin_at=origin, metadata=metadata)
        historical['start_date'] = body.date.isoformat()
        response.update(historical=historical, finished=body.date == LAST_DAY)
        return response

    @app.post("/api/firms")
    def firms(body: BoundsInput | None = None, landscape_mode: bool = False):
        
        current = None if landscape_mode else runtime.ready_model()
        bounds = (body.west, body.south, body.east, body.north) if body else DEFAULT_BOUNDS
        # Repeated clicks in the same view reuse a preview for five minutes.
        with runtime.available('firms'):
            now = datetime.now(timezone.utc)
            cache_key = (bounds, landscape_mode)
            cached = runtime.firms_cache.get(cache_key)
            if cached and (now - cached[0]).total_seconds() < 300:
                return cached[1]
            if runtime.firms_last_fetch is not None and monotonic() - runtime.firms_last_fetch < 10:
                raise HTTPException(429, 'Wait ten seconds between uncached satellite requests.', headers={'Retry-After': '10'})
            runtime.firms_last_fetch = monotonic()
            try:
                state, metadata = (runtime.firms_loader or fetch_current_firms)(runtime.settings.firms_key, bounds, now=now)
            except LiveFirmsError as exc:
                raise HTTPException(502 if runtime.settings.firms_key or runtime.firms_loader else 503, str(exc)) from None
            cells = tuple(c for c in state.active_cells if current is None or current.landscape.allows_cell(c.cell_id))
            metadata = {**metadata, "water_cells_excluded": len(state.active_cells) - len(cells)}
            # Low satellite brightness does not mean an observed fire has no
            # energy. Use the existing scale with a small positive seed floor.
            cells = tuple(replace(c, intensity=max(.1, c.intensity)) for c in cells)
            response = state_response(replace(state, active_cells=cells), origin_at=now, metadata=metadata)
            if len(runtime.firms_cache) >= 16:
                runtime.firms_cache.pop(next(iter(runtime.firms_cache)))
            runtime.firms_cache[cache_key] = (now, response)
            return response

    @app.post('/api/vegetation')
    def vegetation(body: VegetationInput):
        try:
            with runtime.available('vegetation'):
                return vegetation_summary(runtime.vegetation, body.cell_id,
                                          origin_at=body.origin_at, simulation_at=body.simulation_at)
        except HTTPException:
            raise
        except Exception:
            logging.getLogger(__name__).exception('Could not sample vegetation for the cell inspector')
            raise HTTPException(503, 'Vegetation information is temporarily unavailable.') from None

    from wildfire_data.web.landscape_spread import register_expanding_routes
    register_expanding_routes(app, lambda body: firms(body, landscape_mode=True),
                             lambda body: historical_firms(body, landscape_mode=True), historical_day)
