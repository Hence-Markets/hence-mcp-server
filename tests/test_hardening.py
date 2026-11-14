"""What a server on the internet must hold to, whoever is on the other end.

  * the request body is exactly Content-Length bytes: a chunked body is refused and the
    connection closed, so its bytes can never be read as the next request;
  * a connection that stops sending is dropped, and connections are counted;
  * a redirect is an answer, never followed: it would carry a credential or a signature
    to wherever it points;
  * what the app sends is passed on as bounded plain data, and one bad value never turns a
    whole answer into an internal error.
"""
import http.client
import json
import threading
import unittest
import unittest.mock

from gateway import app as gateway_app
from gateway import appauth, config, shape, upstream
from hence_cli import client

from .support import StubUpstream, standard_routes


class Transport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        cls.env = unittest.mock.patch.dict(os.environ, {'HENCE_MCP_ACTIVE': '1'})
        cls.env.start()
        cls.server = gateway_app.make_server('127.0.0.1', 0, upstream=StubUpstream(standard_routes()))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.env.stop()

    def raw(self, lines, body=b''):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        c.putrequest('POST', '/mcp', skip_accept_encoding=True)
        for k, v in lines:
            c.putheader(k, v)
        c.endheaders(body)
        r = c.getresponse()
        out = (r.status, r.getheader('Connection'), r.read())
        c.close()
        return out

    def test_a_chunked_body_is_refused_and_the_connection_closed(self):
        status, _, body = self.raw([('Transfer-Encoding', 'chunked'), ('Content-Type', 'application/json')], b'5\r\nhello\r\n0\r\n\r\n')
        self.assertEqual(status, 411)
        self.assertIn('Content-Length', json.loads(body)['error']['message'])

    def test_a_length_that_is_not_plain_digits_is_not_a_length(self):
        for bad in ('1_0', '+5', '-1', '0x10', '1e3', '9' * 12):
            status, _, _ = self.raw([('Content-Length', bad), ('Content-Type', 'application/json')])
            self.assertEqual(status, 413, bad)
        self.assertTrue(gateway_app._LENGTH.match('0'))
        self.assertIsNone(gateway_app._LENGTH.match('٣'))                    # a digit of another script

    def test_connections_have_a_read_timeout_and_are_counted(self):
        handler = gateway_app.make_handler(StubUpstream({}), limiter=None)
        self.assertEqual(handler.timeout, config.SOCKET_TIMEOUT_S)
        self.assertTrue(0 < config.SOCKET_TIMEOUT_S <= 60)
        self.assertTrue(16 <= config.MAX_CONNECTIONS <= 4096)
        self.assertIsInstance(self.server._slots, type(threading.BoundedSemaphore(1)))
        # a full server closes a new connection at once instead of queueing it without bound
        full = gateway_app.Server.__new__(gateway_app.Server)
        full._slots = threading.BoundedSemaphore(1)
        full._slots.acquire()
        closed = []
        full.shutdown_request = closed.append
        full.process_request('a connection', ('127.0.0.1', 1))
        self.assertEqual(closed, ['a connection'])


class Redirects(unittest.TestCase):
    def test_a_redirect_is_never_followed(self):
        for module in (client, appauth):
            handler = module._NoRedirect()
            self.assertIsNone(handler.redirect_request(None, None, 302, 'Found', {}, 'https://elsewhere.example/'), module.__name__)
            with open(module.__file__, encoding='utf-8') as f:
                source = f.read()
            self.assertIn('_OPENER.open(req, timeout=timeout)', source, module.__name__)
            self.assertNotIn('urlopen(', source, module.__name__)

    def test_an_answer_larger_than_the_cap_is_not_an_answer(self):
        self.assertTrue(2 * 1024 * 1024 <= config.MAX_UPSTREAM_BYTES <= 64 * 1024 * 1024)       # the Solana catalog is about 1.2 MB
        with open(upstream.__file__, encoding='utf-8') as f:
            self.assertIn('raw = r.read(config.MAX_UPSTREAM_BYTES + 1)', f.read())

    def test_a_whole_list_read_outlives_a_spray_of_search_words(self):
        calls = []
        up = upstream.Upstream(base='http://app', fetch=lambda url, timeout: (calls.append(url) or 200, b'{"ok": true}'))
        up.MAX_ENTRIES = 8
        up.get_json('/api/solana/assets', ttl=300)
        for i in range(40):
            up.get_json('/api/research/asset', {'id': 'T%d' % i}, ttl=300)
        up.get_json('/api/solana/assets', ttl=300)
        self.assertEqual(sum(1 for u in calls if u.endswith('/api/solana/assets')), 1)


class PlainData(unittest.TestCase):
    def test_only_bounded_plain_data_is_passed_on(self):
        self.assertEqual(shape.plain({'a': 1, 'b': [1.5, 'x', None, True], 'c': {'d': 'e'}}), {'a': 1, 'b': [1.5, 'x', None, True], 'c': {'d': 'e'}})
        self.assertEqual(shape.plain({'n': float('nan'), 'i': float('inf'), 'o': object(), 't': (1, 2), 7: 'x', 'k' * 81: 1}), {'n': None, 'i': None, 'o': None, 't': None})
        self.assertEqual(len(shape.plain('x' * 9000)), 4000)
        self.assertEqual(len(shape.plain(list(range(900)))), 500)
        self.assertEqual(len(shape.plain({str(i): i for i in range(300)})), 120)
        deep = current = {}
        for _ in range(12):
            current['x'] = {}
            current = current['x']
        self.assertIsNone(shape.plain(deep, 3)['x']['x']['x'])                      # what lies deeper than the bound is dropped, not followed

    def test_a_point_with_a_time_that_is_not_one_is_skipped_not_fatal(self):
        points = [{'t': 1793000000, 'p': 0.5}, {'t': 10 ** 30, 'p': 0.6}, {'t': 1793086400, 'p': 0.7}]
        history = shape._outcome_history(points)
        self.assertEqual([p['close'] for p in history['points']], [0.5, 0.7])

    def test_what_the_app_did_not_say_about_permissions_is_unknown(self):
        from gateway import tools

        class Account:
            def get(self, path):
                return {'handle': 'someone', 'agent': 'Claude'}
        self.assertIsNone(tools.get_my_account({}, None, Account())['allowed'])


if __name__ == '__main__':
    unittest.main()
