"""The real server on a real socket, and the app-reading layer.

The server here is given a stub for the app, so nothing leaves the machine.
"""
import http.client
import json
import os
import threading
import unittest
from unittest import mock

from gateway import app, config
from gateway.limits import RateLimiter
from gateway.upstream import Expired, NotFound, Unavailable, Upstream

from .support import StubUpstream, standard_routes

V = '2026-07-28'
META = {'io.modelcontextprotocol/protocolVersion': V, 'io.modelcontextprotocol/clientCapabilities': {}}


class Served(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {'HENCE_MCP_ACTIVE': '1', 'HENCE_PUBLIC_BASE': 'https://app.example',
                                               'HENCE_MCP_RATE_PER_MIN': '1000'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.up = StubUpstream(standard_routes())
        self.server = app.make_server('127.0.0.1', 0, upstream=self.up, limiter=RateLimiter())
        self.port = self.server.server_address[1]
        t = threading.Thread(target=self.server.serve_forever, daemon=True)
        t.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            raw = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
            conn.request(method, path, body=raw, headers=headers or {})
            r = conn.getresponse()
            data = r.read()
            return r.status, dict((k.lower(), v) for k, v in r.getheaders()), (json.loads(data) if data else None)
        finally:
            conn.close()

    def call(self, method, params=None, extra=None, path='/mcp'):
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
                   'MCP-Protocol-Version': V, 'Mcp-Method': method}
        headers.update(extra or {})
        return self.request('POST', path, {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': dict(params or {}, _meta=META)}, headers)

    def test_health_is_green_and_says_whether_the_tools_are_open(self):
        status, _, body = self.request('GET', '/healthz')
        self.assertEqual((status, body['ok'], body['open']), (200, True, True))
        with mock.patch.dict(os.environ, {'HENCE_MCP_ACTIVE': '0'}):
            status, _, body = self.request('GET', '/healthz')
            self.assertEqual((status, body['ok'], body['open']), (200, True, False))   # dark, but healthy
            self.assertEqual(self.call('tools/list')[0], 503)

    def test_a_tool_call_end_to_end(self):
        status, headers, body = self.call('tools/call', {'name': 'search_markets', 'arguments': {'query': 'btc', 'kind': 'perp'}},
                                          {'Mcp-Name': 'search_markets'})
        self.assertEqual(status, 200)
        self.assertTrue(headers['content-type'].startswith('application/json'))
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertEqual(body['result']['structuredContent']['markets'][0]['market_id'], 'perp:BTC')

    def test_the_bare_address_is_also_the_endpoint(self):
        self.assertEqual(self.call('tools/list', path='/')[0], 200)
        self.assertEqual(self.request('GET', '/')[2]['mcp_endpoint'], '/mcp')

    def test_no_streams_and_no_sessions(self):
        for verb in ('GET', 'DELETE'):
            status, headers, _ = self.request(verb, '/mcp')
            self.assertEqual((status, headers.get('allow')), (405, 'POST'), verb)
        _, headers, _ = self.call('tools/list', extra={'Mcp-Session-Id': 'abc'})
        self.assertNotIn('mcp-session-id', headers)

    def test_a_browser_origin_must_be_on_the_list(self):
        self.assertEqual(self.call('tools/list', extra={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.call('tools/list', extra={'Origin': 'https://app.example'})[0], 200)
        self.assertEqual(self.call('tools/list', extra={'Origin': 'http://localhost:6274'})[0], 200)
        self.assertEqual(self.call('tools/list')[0], 200)                    # no Origin: a server-side client

    def test_an_oversized_body_is_refused(self):
        # The server refuses on the DECLARED length, before reading a byte of the body. So send
        # only the headers: writing the whole body raced the server's close and, on a busy
        # machine, the test died with a connection reset instead of reading the 413.
        import socket
        with socket.create_connection(('127.0.0.1', self.port), timeout=10) as sock:
            sock.sendall(('POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n'
                          'Content-Length: %d\r\n\r\n' % (config.MAX_BODY_BYTES + 1)).encode())
            answer = b''
            while b'\r\n' not in answer:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                answer += chunk
        self.assertEqual(answer.split(b'\r\n')[0].split(b' ')[1], b'413')

    def test_unknown_paths(self):
        self.assertEqual(self.request('GET', '/api/me')[0], 404)
        self.assertEqual(self.request('POST', '/api/exchange', {'x': 1}, {'Content-Type': 'application/json'})[0], 404)

    def test_a_flood_from_one_address_is_slowed(self):
        with mock.patch.dict(os.environ, {'HENCE_MCP_RATE_PER_MIN': '3', 'HENCE_MCP_CLIENT_IP_HEADER': 'CF-Connecting-IP'}):
            codes = [self.call('tools/list', extra={'CF-Connecting-IP': '203.0.113.9'})[0] for _ in range(5)]
            self.assertEqual(codes, [200, 200, 200, 429, 429])
            self.assertEqual(self.call('tools/list', extra={'CF-Connecting-IP': '203.0.113.10'})[0], 200)   # someone else is fine

    def test_a_caller_cannot_pick_its_own_rate_limit_key(self):
        # no trusted header configured: a spoofed CF-Connecting-IP must not open a fresh allowance
        with mock.patch.dict(os.environ, {'HENCE_MCP_RATE_PER_MIN': '2'}):
            codes = [self.call('tools/list', extra={'CF-Connecting-IP': '203.0.113.%d' % i})[0] for i in range(4)]
            self.assertEqual(codes, [200, 200, 429, 429])


class Limiter(unittest.TestCase):
    def test_the_window_slides(self):
        now = [0.0]
        lim = RateLimiter(clock=lambda: now[0])
        self.assertEqual([lim.allow('a', 2) for _ in range(3)], [True, True, False])
        now[0] = 61.0
        self.assertTrue(lim.allow('a', 2))


class AppReads(unittest.TestCase):
    """upstream.Upstream: only a 200 with JSON is data, and only data is cached."""

    def make(self, answers):
        self.fetched = []
        self.now = [1000.0]

        def fetch(url, timeout):
            self.fetched.append(url)
            a = answers.pop(0)
            if isinstance(a, BaseException):
                raise a
            return a

        return Upstream(base='http://app.test', fetch=fetch, clock=lambda: self.now[0])

    def test_a_good_answer_is_cached_for_its_ttl_only(self):
        up = self.make([(200, b'{"n": 1}'), (200, b'{"n": 2}')])
        self.assertEqual(up.get_json('/x', {'a': 1, 'skip': None, 'blank': ''}, ttl=60), {'n': 1})
        self.assertEqual(self.fetched, ['http://app.test/x?a=1'])
        self.assertEqual(up.get_json('/x', {'a': 1}, ttl=60), {'n': 1})
        self.now[0] += 61
        self.assertEqual(up.get_json('/x', {'a': 1}, ttl=60), {'n': 2})
        self.assertEqual(len(self.fetched), 2)

    def test_a_failure_is_typed_and_never_cached(self):
        up = self.make([(503, b'down'), (200, b'{"ok": true}')])
        with self.assertRaises(Unavailable) as ctx:
            up.get_json('/x', ttl=60)
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(up.get_json('/x', ttl=60), {'ok': True})           # the retry reads again

    def test_each_kind_of_non_answer(self):
        for answer, exc in (((404, b'{}'), NotFound), ((409, b'{}'), Expired), ((500, b''), Unavailable),
                            ((200, b'<html>'), Unavailable), ((302, b''), Unavailable), (TimeoutError('slow'), Unavailable),
                            (ConnectionRefusedError(), Unavailable)):
            with self.assertRaises(exc, msg=repr(answer)):
                self.make([answer]).get_json('/x', ttl=60)

    def test_the_cache_is_bounded(self):
        up = self.make([(200, b'{}')] * (Upstream.MAX_ENTRIES + 50))
        for i in range(Upstream.MAX_ENTRIES + 50):
            up.get_json('/x', {'i': i}, ttl=600)
        self.assertLessEqual(len(up._cache), Upstream.MAX_ENTRIES)


if __name__ == '__main__':
    unittest.main()
