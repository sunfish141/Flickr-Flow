"""Local-preview HTTP boundaries; public deployments still need an edge gateway."""

import asyncio
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_INFLIGHT_REQUESTS = 8
SECURITY_HEADERS = {
    'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https://tile.openstreetmap.org; connect-src 'self'; "
        "font-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'",
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
    'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=()',
    'Cache-Control': 'no-store',
}


class RequestBoundary:
    """Check origin, bound streamed bodies, and reject excess work before parsing.

    In-flight accounting is local to the ASGI event loop/process. Endpoint locks
    also protect actual synchronous work, including after browser cancellation.
    """

    def __init__(self, app):
        self.app = app
        self.inflight = 0

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)

        async def secured_send(message):
            if message['type'] == 'http.response.start':
                headers = list(message.get('headers', []))
                names = {key.encode().lower() for key in SECURITY_HEADERS}
                message['headers'] = [(key, value) for key, value in headers if key.lower() not in names] + [
                    (key.lower().encode(), value.encode()) for key, value in SECURITY_HEADERS.items()]
            await send(message)

        async def reject(status, detail, headers=None):
            await JSONResponse({'detail': detail}, status_code=status, headers=headers)(scope, receive, secured_send)

        path = scope['path'].removeprefix(scope.get('root_path', ''))
        if not path.startswith('/api/'):
            return await self.app(scope, receive, secured_send)
        headers = {key.lower(): value for key, value in scope['headers']}
        host = headers.get(b'host', b'').decode('latin-1')
        origin = headers.get(b'origin')
        if origin:
            try:
                parsed = urlsplit(origin.decode('latin-1'))
                same_origin = (parsed.scheme == scope['scheme'] and parsed.netloc.lower() == host.lower()
                               and not parsed.path and not parsed.query and not parsed.fragment)
            except ValueError:
                same_origin = False
            if not same_origin:
                return await reject(403, 'Cross-origin API requests are not allowed.')
        if headers.get(b'sec-fetch-site') == b'cross-site':
            return await reject(403, 'Cross-site API requests are not allowed.')
        if self.inflight >= MAX_INFLIGHT_REQUESTS:
            return await reject(503, 'Server busy. Try again shortly.', {'Retry-After': '3'})
        if b'content-length' in headers:
            try:
                length = int(headers[b'content-length'])
                if length < 0:
                    raise ValueError
            except ValueError:
                return await reject(400, 'Invalid Content-Length.')
            if length > MAX_REQUEST_BYTES:
                return await reject(413, 'Request exceeds the 8 MiB preview limit.')
        self.inflight += 1
        try:
            body = bytearray()

            async def read_body():
                while True:
                    chunk = await receive()
                    if chunk['type'] == 'http.disconnect':
                        return False
                    body.extend(chunk.get('body', b''))
                    if len(body) > MAX_REQUEST_BYTES:
                        return None
                    if not chunk.get('more_body', False):
                        return True

            try:
                complete = await asyncio.wait_for(read_body(), timeout=15)
            except TimeoutError:
                return await reject(408, 'Request body timed out.')
            if complete is False:
                return
            if complete is None:
                return await reject(413, 'Request exceeds the 8 MiB preview limit.')
            if body and headers.get(b'content-type', b'').split(b';')[0].strip().lower() != b'application/json':
                return await reject(415, 'Use application/json for API requests.')
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                return await receive()

            await self.app(scope, replay, secured_send)
        finally:
            self.inflight -= 1
