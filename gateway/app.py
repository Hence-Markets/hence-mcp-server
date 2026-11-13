"""The HTTP face: one MCP endpoint and a health check, on the standard library's ThreadingHTTPServer.

  POST /mcp      the MCP endpoint (also accepted at / so a pasted bare address works)
  GET  /healthz  container health; green even while the kill switch is off
  GET  /         a small description for a person who opens the address in a browser
  GET|DELETE /mcp -> 405 (no streams, no sessions: see protocol.py)
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import config, protocol
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


def make_handler(upstream, limiter):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'HenceMCP/' + config.VERSION
        protocol_version = 'HTTP/1.1'

        def log_message(self, *a):  # request lines stay out of the logs; tool outcomes go in
            pass

        def _send(self, status, payload=None, extra=None):
            body = b'' if payload is None else json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            self.send_response(status)
            if body:
                self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            for k, v in (extra or {}).items():
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
            try:
                length = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                length = -1
            if length < 0 or length > config.MAX_BODY_BYTES:
                self.close_connection = True  # the unread body would poison the next request on this connection
                return self._send(413, protocol._error(None, protocol.INVALID_REQUEST, 'The request is too large.'))
            raw = self.rfile.read(length) if length else b''
            if not limiter.allow(self._client(), config.rate_per_minute()):
                return self._send(429, protocol._error(None, protocol.NOT_OPEN, 'Too many requests. Slow down and retry.'),
                                  {'Retry-After': '10'})
            headers = {k.lower(): v for k, v in self.headers.items()}
            status, payload = protocol.handle(headers, raw, upstream, log=_log)
            self._send(status, payload)

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128  # a hosted assistant opens many connections at once


def make_server(host=None, port=None, upstream=None, limiter=None):
    return Server((host if host is not None else config.host(), port if port is not None else config.port()),
                  make_handler(upstream or Upstream(), limiter or RateLimiter()))


def main():
    server = make_server()
    _log('listening on %s:%d, reading %s, open=%s' % (config.host(), config.port(), config.app_base(), config.active()))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    sys.exit(main())
