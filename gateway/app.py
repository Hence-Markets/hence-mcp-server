"""The HTTP face: one MCP endpoint and a health check, on the standard library's ThreadingHTTPServer.

  POST /mcp      the MCP endpoint (also accepted at / so a pasted bare address works)
  GET  /healthz  container health; green even while the kill switch is off
  GET  /         a small description for a person who opens the address in a browser
  GET|DELETE /mcp -> 405 (no streams, no sessions: see protocol.py)
"""
import json
import re
import threading
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import config, protocol
from .appauth import AppAuth
from .limits import RateLimiter
from .upstream import Upstream

MCP_PATHS = ('/mcp', '/')


def _log(line):
    print('[hence-mcp] ' + line, flush=True)


def _origin_allowed(origin):
    """The spec requires an Origin check (DNS rebinding). No Origin = a server-side client,
    which is every hosted assistant and coding agent. A browser page must be on the list;
    localhost is allowed so a developer's inspector can connect."""
    if not origin:
        return True
    o = origin.strip().rstrip('/').lower()
    if o in config.allowed_origins():
        return True
    host = (urlparse(o).hostname or '')
    return host in ('localhost', '127.0.0.1', '::1')


_LENGTH = re.compile(r'^[0-9]{1,9}$')


def make_handler(upstream, limiter, auth=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'HenceMCP/' + config.VERSION
        protocol_version = 'HTTP/1.1'
        # A read timeout on every connection. Without one, a client that opens a connection and sends half
        # a header (or holds an idle keep-alive) holds its thread for ever, and enough of them hold them all.
        timeout = config.SOCKET_TIMEOUT_S

        def log_message(self, *a):  # request lines stay out of the logs; tool outcomes go in
            pass

        def _send(self, status, payload=None, extra=None):
            body = b'' if payload is None else json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            self.send_response(status)
            if body:
                self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            extra = dict(extra or {})
            self.send_header('Cache-Control', extra.pop('Cache-Control', 'no-store'))
            self.send_header('X-Content-Type-Options', 'nosniff')
            for k, v in extra.items():
                self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _client(self):
            # behind a proxy the peer is the proxy; only a header the operator named is believed
            name = config.trusted_client_header()
            stated = self.headers.get(name) if name else None
            return (stated or self.client_address[0] or '').strip()

        def _path(self):
            return urlparse(self.path).path.rstrip('/') or '/'

        def do_GET(self):
            path = self._path()
            if path == '/healthz':
                return self._send(200, {'ok': True, 'version': config.VERSION, 'open': config.active()})
            if path == '/mcp':
                return self._send(405, protocol._error(None, protocol.INVALID_REQUEST, 'This endpoint accepts POST only.'), {'Allow': 'POST'})
            if path.startswith('/.well-known/oauth-protected-resource'):
                # how a client learns where to sign people in (RFC 9728). Absent while sign-in is
                # off, so a client sees a server that needs no credentials at all.
                known = {'/.well-known/oauth-protected-resource', urlparse(protocol.resource_metadata_url()).path}
                if config.accounts_active() and path in known:
                    return self._send(200, protocol.resource_metadata(), {'Cache-Control': 'public, max-age=300'})
                return self._send(404, {'error': 'not found'})
            if path == '/':
                return self._send(200, {
                    'name': 'Hence MCP server', 'version': config.VERSION,
                    'mcp_endpoint': '/mcp', 'transport': 'Streamable HTTP',
                    'protocol_versions': list(protocol.SUPPORTED),
                    'what': 'Read-only tools over Hence: public theses and tradable markets.',
                    'app': config.public_base()})
            return self._send(404, {'error': 'not found'})

        def do_DELETE(self):
            if self._path() == '/mcp':
                return self._send(405, protocol._error(None, protocol.INVALID_REQUEST, 'This endpoint accepts POST only.'), {'Allow': 'POST'})
            return self._send(404, {'error': 'not found'})

        def do_POST(self):
            if self._path() not in MCP_PATHS:
                return self._send(404, {'error': 'not found'})
            if not _origin_allowed(self.headers.get('Origin')):
                return self._send(403, protocol._error(None, protocol.INVALID_REQUEST, 'This origin may not call the endpoint.'))
            # The body is exactly Content-Length bytes and nothing else. A chunked body is refused, not ignored:
            # this server does not decode it, so its bytes would stay on the connection and be read as the NEXT
            # request (a proxy that pools connections would then pair one caller's bytes with another's request).
            if self.headers.get('Transfer-Encoding'):
                self.close_connection = True
                return self._send(411, protocol._error(None, protocol.INVALID_REQUEST, 'Send the request with a Content-Length.'))
            declared = (self.headers.get('Content-Length') or '').strip()
            # plain ASCII digits only: int() also takes "1_0", " +5" and digits of other scripts
            length = int(declared) if _LENGTH.match(declared) else (0 if not declared else -1)
            if length < 0 or length > config.MAX_BODY_BYTES:
                self.close_connection = True  # the unread body would poison the next request on this connection
                return self._send(413, protocol._error(None, protocol.INVALID_REQUEST, 'The request is too large.'))
            raw = self.rfile.read(length) if length else b''
            if not limiter.allow(self._client(), config.rate_per_minute()):
                return self._send(429, protocol._error(None, protocol.NOT_OPEN, 'Too many requests. Slow down and retry.'),
                                  {'Retry-After': '10'})
            headers = {k.lower(): v for k, v in self.headers.items()}
            status, payload, extra = protocol.handle_full(headers, raw, upstream, log=_log, auth=auth)
            self._send(status, payload, extra)

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128  # a hosted assistant opens many connections at once

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # one thread per connection, so connections are counted: past the limit a new one is closed at once
        self._slots = threading.BoundedSemaphore(config.MAX_CONNECTIONS)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def make_server(host=None, port=None, upstream=None, limiter=None, auth=None):
    return Server((host if host is not None else config.host(), port if port is not None else config.port()),
                  make_handler(upstream or Upstream(), limiter or RateLimiter(), auth or AppAuth()))


def main():
    server = make_server()
    _log('listening on %s:%d, reading %s, open=%s, sign-in=%s' % (config.host(), config.port(), config.app_base(), config.active(), config.accounts_active()))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    sys.exit(main())
