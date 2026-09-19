"""Loopback-only offline service. No legacy runtime, dotenv or providers loaded."""
from contextlib import asynccontextmanager
import os
from pathlib import Path
import sqlite3
import sys
from uuid import UUID

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from wildfire_data.planning.contracts import (Clone, Create, Cursor, Definition, LIMITATIONS,
    Mutation, Policy, Portable, Strict, Update, canonical, engine_identity)
from wildfire_data.planning.exports import geojson, html_report
from wildfire_data.planning.packs import Packs
from wildfire_data.planning.paths import InstanceLock, data_directory, resource_directory
from wildfire_data.planning.service import Service
from wildfire_data.planning.store import Conflict, StorageFull, Store, tree_bytes
from wildfire_data.web.security import RequestBoundary

STATIC = Path(__file__).with_name('static')


class OfflineHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def secure(message):
            if message['type'] == 'http.response.start':
                message['headers'] = [(k, v) for k, v in message.get('headers', []) if k.lower() != b'content-security-policy']
                message['headers'].append((b'content-security-policy',
                    b"default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'"))
            await send(message)
        await self.app(scope, receive, secure)


class ImportRequest(Strict):
    request_id: UUID
    document: Portable


def create_app(*, data_root=None, pack_root=None, installed_bytes=None):
    os.environ['PROJ_NETWORK'] = 'OFF'
    root, packs_path = Path(data_root or data_directory()), Path(pack_root or resource_directory())

    @asynccontextmanager
    async def lifespan(app):
        instance = InstanceLock(root)
        service = None
        try:
            packs = Packs(packs_path)
            # Frozen builds account for their entire installation, including
            # runtime and bundled packs. Development excludes training archives.
            installed = installed_bytes
            if installed is None:
                installed = tree_bytes(Path(sys.executable).parent) if getattr(sys, 'frozen', False) else tree_bytes(packs_path) + tree_bytes(STATIC)
            service = Service(Store(root, installed_bytes=installed), packs)
            app.state.service = service
            yield
        finally:
            try:
                if service:
                    service.close()
            finally:
                instance.close()

    app = FastAPI(title='Wildfire offline planning', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RequestBoundary)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]'], www_redirect=False)
    app.add_middleware(OfflineHeaders)

    @app.exception_handler(RequestValidationError)
    async def invalid(_request, exc):
        return JSONResponse(status_code=422, content={'detail': 'Invalid planning document', 'errors': [
            {k: e[k] for k in ('loc', 'msg', 'type')} for e in exc.errors()]})

    async def failure(_request, exc):
        status = 409 if isinstance(exc, Conflict) else 507 if isinstance(exc, (OSError, sqlite3.Error)) else 404 if isinstance(exc, KeyError) else 422
        return JSONResponse(status_code=status, content={'detail': str(exc), 'saved': False})
    for exception in (ValueError, KeyError, OSError, sqlite3.Error):
        app.add_exception_handler(exception, failure)

    def svc():
        return app.state.service

    @app.get('/api/config')
    def config():
        return {'profile': 'offline-planning', 'offline': True, 'outbound_providers_enabled': False,
            'research_only': True, 'unsigned_internal_build': True, 'engine_version': engine_identity(),
            'model_ready': False, 'modes': ['local-planning'] if svc().packs.entries else [],
            'packs': svc().packs.list(), 'pack_errors': svc().packs.errors, 'limitations': LIMITATIONS,
            'defaults': {'mesh_m': 100, 'horizon_hours': 24, 'output_interval_minutes': 60, 'policy': Policy().model_dump()},
            'storage': svc().store.storage(), 'save_error': svc().worker.volatile_error,
            'numerical_tolerance': {'area_m2_absolute': 1e-6, 'coordinate_degrees_absolute': 1e-9},
            'max_import_bytes': 8*1024*1024, 'performance_targets_measured': False}

    @app.get('/api/packs')
    def packs():
        return {'packs': svc().packs.list(), 'errors': svc().packs.errors}

    @app.get('/api/packs/{identity}')
    def pack(identity: str):
        if identity not in svc().packs.entries:
            raise KeyError('Verified pack not found')
        return svc().packs.entries[identity][1]

    @app.get('/api/scenarios')
    def scenarios():
        return {'scenarios': svc().store.list(), 'save_error': svc().worker.volatile_error}

    @app.post('/api/scenarios')
    def create(request: Create):
        return svc().create(request)

    @app.post('/api/scenarios/import')
    def import_scenario(request: ImportRequest):
        return svc().import_case(request.request_id, request.document)

    @app.get('/api/scenarios/{identity}')
    def get(identity: UUID):
        return svc().get(str(identity))

    @app.put('/api/scenarios/{identity}')
    def update(identity: UUID, request: Update):
        return svc().update(str(identity), request)

    @app.post('/api/scenarios/{identity}/clone')
    def clone(identity: UUID, request: Clone):
        return svc().clone(str(identity), request)

    @app.post('/api/scenarios/{identity}/run')
    def run(identity: UUID, request: Mutation):
        return svc().run(str(identity), request)

    @app.post('/api/scenarios/{identity}/pause')
    def pause(identity: UUID, request: Mutation):
        return svc().pause(str(identity), request)

    @app.put('/api/scenarios/{identity}/playback')
    def cursor(identity: UUID, request: Cursor):
        return svc().cursor(str(identity), request)

    @app.get('/api/scenarios/{identity}/frames')
    def frames(identity: UUID):
        return {'frames': svc().store.frames(str(identity))}

    @app.get('/api/scenarios/{identity}/export')
    def export(identity: UUID, format: str = Query(default='json', pattern='^(json|geojson|html)$')):
        document = svc().document(str(identity))
        payload = html_report(document) if format == 'html' else canonical(geojson(document) if format == 'geojson' else document)
        if format == 'json' and len(payload.encode()) > 8*1024*1024 - 1024:
            raise ValueError('Portable document exceeds the 8 MiB import limit; export HTML/GeoJSON instead. No results were removed.')
        return Response(payload, media_type={'html': 'text/html', 'json': 'application/json', 'geojson': 'application/geo+json'}[format],
            headers={'Content-Disposition': f'attachment; filename="scenario-{identity}.{format}"'})

    if STATIC.exists():
        app.mount('/static', StaticFiles(directory=STATIC), name='static')
    @app.get('/')
    def index():
        return FileResponse(STATIC / 'index.html')
    return app
