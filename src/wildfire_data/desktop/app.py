"""One loopback service and integrated map, with retained legacy APIs."""
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
import os
from pathlib import Path
import sys

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from wildfire_data.desktop.network import NetworkPolicy
from wildfire_data.planning.app import create_app as planning_app
from wildfire_data.planning.paths import data_directory
from wildfire_data.web.application import create_app as web_app, STATIC as WEB_STATIC
from wildfire_data.web.security import RequestBoundary
from wildfire_data.web.settings import Settings

STATIC = Path(__file__).with_name('static')


def resource_root():
    return Path(sys._MEIPASS) / 'desktop_resources' if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[3]


def desktop_settings(root):
    # Never discover adjacent archives, .env files or perform preparation in an
    # installed desktop app. Only bundled models/packs plus an optional env key.
    return Settings(run_manifest=root / 'artifacts/public-csv/run_manifest.json',
                    data_root=root / 'data', local_config=root / 'config/local_spread_prepared.json',
                    fuel_policy=root / 'config/fuel_policy.json',
                    prepare_data=False, download_vegetation=False,
                    firms_key=os.getenv('NASA_FIRMS_API_KEY') or os.getenv('MAP_KEY') or '')


class DesktopHeaders:
    def __init__(self, app, policy):
        self.app, self.policy = app, policy

    async def __call__(self, scope, receive, send):
        planning_scope = scope.get('path', '').startswith('/planning/')
        async def secure(message):
            if message['type'] == 'http.response.start':
                headers = []
                for key, value in message.get('headers', []):
                    if key.lower() == b'x-frame-options':
                        value = b'SAMEORIGIN'
                    if key.lower() == b'content-security-policy':
                        value = value.replace(b"frame-ancestors 'none'", b"frame-ancestors 'self'")
                        # Keep Explorer's allowlist stable: toggling tiles must
                        # not reload and discard a scenario. Offline enforcement
                        # lives in the UI, Python audit and desktop Qt filter.
                        value += b"; frame-src 'self'"
                        if planning_scope:
                            value = value.replace(b' https://tile.openstreetmap.org', b'')
                    headers.append((key, value))
                message['headers'] = headers
            await send(message)
        await self.app(scope, receive, secure)


class OnlineInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool


class KeyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(max_length=256)


def create_app(*, data_root=None, resources=None, policy=None, explorer=None):
    resources = Path(resources or resource_root())
    policy = policy or NetworkPolicy()
    explorer = explorer or web_app(settings=desktop_settings(resources))
    planner = planning_app(data_root=data_root or data_directory(), pack_root=resources / 'data/planning-packs-v1')

    @asynccontextmanager
    async def lifespan(app):
        # Mounted FastAPI applications do not start their own lifespans.
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(planner.router.lifespan_context(planner))
            await stack.enter_async_context(explorer.router.lifespan_context(explorer))
            explorer.state.runtime.network_policy = policy
            app.state.planner, app.state.explorer = planner, explorer
            yield

    app = FastAPI(title='Wildfire Atlas Desktop', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RequestBoundary)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]'], www_redirect=False)
    app.add_middleware(DesktopHeaders, policy=policy)

    @app.exception_handler(RequestValidationError)
    async def invalid(_request, _exc):
        return JSONResponse(status_code=422, content={'detail': 'Invalid desktop setting'})

    @app.get('/api/desktop')
    def status():
        runtime = explorer.state.runtime
        return {**policy.status(), 'research_only': True, 'unsigned_internal_build': True,
                'firms_configured': bool(runtime.settings.firms_key or runtime.firms_loader),
                'model_ready': runtime.model_error is None,
                'packs': planner.state.service.packs.list(),
                'pack_errors': planner.state.service.packs.errors}

    @app.post('/api/desktop/connectivity')
    def network(body: OnlineInput):
        policy.set_online(body.enabled)
        return status()

    @app.post('/api/desktop/firms-key')
    def key(body: KeyInput):
        value = body.key.strip()
        if any(c.isspace() for c in value):
            raise HTTPException(422, 'The key cannot contain whitespace')
        runtime = explorer.state.runtime
        with runtime.available('firms'):
            runtime.settings = replace(runtime.settings, firms_key=value)
            runtime.firms_cache.clear()
        return status()  # Never return, log or persist the credential.

    @app.get('/')
    def index():
        return FileResponse(STATIC / 'index.html')

    @app.get('/explore/')
    def explore():
        return FileResponse(WEB_STATIC / 'index.html')

    app.mount('/desktop-static', StaticFiles(directory=STATIC), name='desktop-static')
    app.mount('/planning', planner)
    app.mount('/', explorer)
    return app
