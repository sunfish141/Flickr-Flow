"""Standalone FastAPI application, serving the compiled React workspace."""
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from wildfire_data.web.settings import Settings
from wildfire_data.web.runtime import Runtime
from wildfire_data.web.routes import register_routes
from wildfire_data.web.security import RequestBoundary
from wildfire_data.web.local_spread import register_local_routes

STATIC = Path(__file__).with_name('static')


def create_app(*, settings=None, model=None, terrain_provider=None, firms_loader=None,
               landscape=None, vegetation_sampler=None, allowed_hosts=None,
               historical_store=None, local_config=None):
    load_defaults = settings is not None or model is None or local_config is not None
    injected_defaults = settings is None and model is not None
    settings = settings or Settings.from_environment()
    if injected_defaults:
        settings = replace(settings, prepare_data=False)
    if local_config is not None:
        settings = replace(settings, local_config=Path(local_config))
    runtime = Runtime(settings, model=model, terrain_provider=terrain_provider,
        firms_loader=firms_loader, landscape=landscape,
        vegetation_sampler=vegetation_sampler, historical_store=historical_store,
        load_defaults=load_defaults)

    @asynccontextmanager
    async def lifespan(app):
        runtime.start()
        # Local geometry routes use this shared runtime-owned resource.
        app.state.local_scenarios = runtime.local_scenarios
        app.state.runtime = runtime
        try:
            yield
        finally:
            runtime.close()

    app = FastAPI(title='Wildfire Atlas', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware,
        allowed_hosts=allowed_hosts or list(settings.allowed_hosts), www_redirect=False)
    app.add_middleware(RequestBoundary)
    app.mount('/static', StaticFiles(directory=STATIC), name='static')

    @app.get('/')
    def index():
        return FileResponse(STATIC / 'index.html')

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, exc):
        return JSONResponse(status_code=422, content={'detail': [
            {key: error[key] for key in ('loc', 'msg', 'type')} for error in exc.errors()]})

    register_local_routes(app)
    register_routes(app, runtime)
    return app


