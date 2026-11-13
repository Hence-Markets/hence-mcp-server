"""Signing in: a person connects their Hence account and a tool reads it.

What these pin:
  * signed-in tools exist only while sign-in is switched on AND configured;
  * a public tool never looks at a credential;
  * no credential, a dead one, and a missing permission each get the HTTP challenge that
    sends the person to Hence, while "Hence did not answer" is never treated as "sign in again";
  * this server signs its own calls and never forwards the person's token to a data route;
  * the signature is the one the Hence app computes (shared test vectors).
"""
import http.client
import json
import os
import threading
import unittest
from unittest import mock

from gateway import app, appauth, protocol, tools
from gateway.limits import RateLimiter

from .support import StubUpstream, standard_routes

V = '2026-07-28'
SECRET = 'k' * 40
TOKEN = 'hat_' + 'A' * 43
GRANT = 'ag_' + 'a' * 20
ON = {'HENCE_MCP_ACTIVE': '1', 'HENCE_MCP_ACCOUNTS': '1', 'HENCE_MCP_GATEWAY_SECRET': SECRET,
      'HENCE_MCP_RESOURCE': 'https://mcp.hence.markets/mcp', 'HENCE_AUTH_ISSUER': 'https://app.hence.markets'}
WHOAMI = {'handle': 'alice_trades', 'profile_url': 'https://app.hence.markets/u/alice_trades', 'agent': 'Example Agent', 'scopes': ['account:read']}


def modern(method, params=None, headers=None, name=None):
    p = dict(params or {})
    p['_meta'] = {'io.modelcontextprotocol/protocolVersion': V}
    h = {'mcp-protocol-version': V, 'mcp-method': method}
    if name:
        h['mcp-name'] = name
    h.update(headers or {})
    return h, json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': p}).encode()


def call_account(headers=None):
    return modern('tools/call', {'name': 'get_my_account', 'arguments': {}}, headers, 'get_my_account')


class StubAuth:
    """Stands in for AppAuth: answers from a table, records what was asked."""

    def __init__(self, connection=None, whoami=None):
        self.connection_answer = {'grant': GRANT, 'scopes': ['account:read'], 'client_name': 'Example Agent'} if connection is None else connection
        self.whoami_answer = WHOAMI if whoami is None else whoami
        self.asked, self.read, self.forgotten = [], [], []

    def connection(self, token):
        self.asked.append(token)
        if isinstance(self.connection_answer, BaseException):
            raise self.connection_answer
        return self.connection_answer

    def get(self, grant, path, query=None):
        self.read.append((grant, path) if query is None else (grant, path, dict(query)))
        if isinstance(self.whoami_answer, BaseException):
            raise self.whoami_answer
        return self.whoami_answer

    def forget(self, token):
        self.forgotten.append(token)


class Env(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, ON)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.up = StubUpstream(standard_routes())


class SwitchedOnOnlyWhenConfigured(Env):
    def names(self):
        _, out = protocol.handle(*modern('tools/list'), self.up)
        return [t['name'] for t in out['result']['tools']]

    def test_the_account_tool_is_listed_only_with_the_switch_and_the_secret(self):
        self.assertEqual(self.names()[5:], ['get_my_account', 'list_my_theses', 'get_my_thesis', 'get_my_portfolio', 'check_thesis', 'save_thesis_draft'])
        self.assertEqual(self.names()[:5], [t['name'] for t in tools.TOOLS])            # public tools keep their order
        for change in ({'HENCE_MCP_ACCOUNTS': '0'}, {'HENCE_MCP_ACCOUNTS': 'true'}, {'HENCE_MCP_GATEWAY_SECRET': ''}, {'HENCE_MCP_GATEWAY_SECRET': 'x' * 31}):
            with mock.patch.dict(os.environ, change):
                self.assertNotIn('get_my_account', self.names(), change)
                status, out = protocol.handle(*call_account({'authorization': 'Bearer ' + TOKEN}), self.up, auth=StubAuth())
                self.assertEqual((status, out['error']['code']), (200, -32602), change)      # an unknown tool, not a sign-in prompt

    def test_the_listing_never_shows_the_permission_or_the_handler(self):
        _, out = protocol.handle(*modern('tools/list'), self.up)
        blob = json.dumps(out)
        self.assertNotIn('handler', blob)
        self.assertNotIn('"scope"', blob)


class Challenges(Env):
    def run_(self, headers=None, auth=None):
        return protocol.handle_full(*call_account(headers), self.up, auth=auth or StubAuth())

    def test_no_credential_is_a_401_that_says_where_to_sign_in(self):
        auth = StubAuth()
        status, out, headers = self.run_(auth=auth)
        self.assertEqual(status, 401)
        self.assertEqual(headers['WWW-Authenticate'],
                         'Bearer resource_metadata="https://mcp.hence.markets/.well-known/oauth-protected-resource/mcp", scope="account:read"')
        self.assertEqual(out['error']['code'], protocol.SIGN_IN)
        self.assertEqual((auth.asked, auth.read), ([], []))
        for junk in ('', 'Basic abc', 'Bearer', 'Bearer   '):
            self.assertEqual(self.run_({'authorization': junk})[0], 401, junk)

    def test_a_dead_credential_is_a_401_invalid_token(self):
        status, _, headers = self.run_({'authorization': 'Bearer ' + TOKEN}, StubAuth(connection=appauth.NotConnected()))
        self.assertEqual(status, 401)
        self.assertTrue(headers['WWW-Authenticate'].startswith('Bearer error="invalid_token", resource_metadata='))

    def test_a_missing_permission_is_a_403_naming_it(self):
        for auth in (StubAuth(connection={'grant': GRANT, 'scopes': [], 'client_name': 'x'}), StubAuth(whoami=appauth.MissingScope('account:read'))):
            status, _, headers = self.run_({'authorization': 'Bearer ' + TOKEN}, auth)
            self.assertEqual(status, 403)
            self.assertIn('error="insufficient_scope"', headers['WWW-Authenticate'])
            self.assertIn('scope="account:read"', headers['WWW-Authenticate'])

    def test_a_connection_closed_mid_session_asks_for_sign_in_and_is_forgotten(self):
        auth = StubAuth(whoami=appauth.NotConnected())
        status, _, headers = self.run_({'authorization': 'Bearer ' + TOKEN}, auth)
        self.assertEqual(status, 401)
        self.assertIn('invalid_token', headers['WWW-Authenticate'])
        self.assertEqual(auth.forgotten, [TOKEN])

    def test_hence_not_answering_is_never_sign_in_again(self):
        for auth in (StubAuth(connection=appauth.Unavailable('Hence could not be reached (TimeoutError)')),
                     StubAuth(whoami=appauth.Unavailable('Hence answered 503'))):
            status, out, headers = self.run_({'authorization': 'Bearer ' + TOKEN}, auth)
            self.assertEqual((status, headers), (200, {}))
            self.assertIs(out['result']['isError'], True)
            self.assertIn('try again shortly', out['result']['content'][0]['text'])
            self.assertNotIn('structuredContent', out['result'])
            self.assertEqual(auth.forgotten, [])

    def test_a_busy_connection_says_how_long_to_wait(self):
        status, out, _ = self.run_({'authorization': 'Bearer ' + TOKEN}, StubAuth(whoami=appauth.Busy('12')))
        self.assertEqual(status, 200)
        self.assertIn('12 seconds', out['result']['content'][0]['text'])

    def test_the_legacy_handshake_gets_the_same_challenge(self):
        body = json.dumps({'jsonrpc': '2.0', 'id': 9, 'method': 'tools/call', 'params': {'name': 'get_my_account', 'arguments': {}}}).encode()
        status, out, headers = protocol.handle_full({'mcp-protocol-version': '2025-06-18'}, body, self.up, auth=StubAuth())
        self.assertEqual((status, out['id']), (401, 9))
        self.assertIn('resource_metadata=', headers['WWW-Authenticate'])


class SignedIn(Env):
    def test_the_account_is_read_under_the_connection(self):
        auth = StubAuth()
        status, out, headers = protocol.handle_full(*call_account({'authorization': 'bearer ' + TOKEN}), self.up, auth=auth)
        self.assertEqual((status, headers), (200, {}))
        got = out['result']['structuredContent']
        self.assertEqual((got['username'], got['profile_url'], got['connected_agent'], got['allowed'], got['note']),
                         ('alice_trades', 'https://app.hence.markets/u/alice_trades', 'Example Agent', ['account:read'], None))
        self.assertEqual((auth.asked, auth.read), ([TOKEN], [(GRANT, '/api/agents/v1/whoami')]))
        self.assertNotIn(TOKEN, json.dumps(out))

    def test_an_account_without_a_username_says_so(self):
        _, out, _ = protocol.handle_full(*call_account({'authorization': 'Bearer ' + TOKEN}), self.up,
                                         auth=StubAuth(whoami=dict(WHOAMI, handle=None, profile_url=None)))
        got = out['result']['structuredContent']
        self.assertEqual((got['username'], got['profile_url']), (None, None))
        self.assertIn('has not claimed a username', got['note'])

    def test_a_public_tool_never_looks_at_the_credential(self):
        auth = StubAuth(connection=appauth.NotConnected())
        for headers in ({}, {'authorization': 'Bearer ' + TOKEN}, {'authorization': 'Bearer garbage'}):
            pair = modern('tools/call', {'name': 'search_markets', 'arguments': {'kind': 'perp'}}, headers, 'search_markets')
            status, out, extra = protocol.handle_full(*pair, self.up, auth=auth)
            self.assertEqual((status, out['result']['isError'], extra), (200, False, {}))
        self.assertEqual(auth.asked, [])

    def test_the_credential_is_never_logged(self):
        lines = []
        protocol.handle_full(*call_account({'authorization': 'Bearer ' + TOKEN}), self.up, log=lines.append, auth=StubAuth())
        protocol.handle_full(*call_account({'authorization': 'Bearer ' + TOKEN}), self.up, log=lines.append,
                             auth=StubAuth(connection=appauth.Unavailable('x')))
        self.assertTrue(lines)
        self.assertNotIn(TOKEN, ' '.join(lines))


class Signature(unittest.TestCase):
    def test_the_same_signature_the_hence_app_computes(self):
        # the same two vectors are pinned in the app's tests/test_agent_connect.py
        a = appauth.sign('k' * 40, 'POST', '/api/agents/v1/introspect', '', b'{"token":"t"}', stamp=1794560000, nonce='0123456789abcdef0123456789abcdef')
        self.assertEqual(a['X-Hence-Agent-Signature'], 'fb610f560eda60b33c0812324abfde9399c28a9dd5685962b2fb8eca1b2bf319')
        b = appauth.sign('k' * 40, 'GET', '/api/agents/v1/whoami', 'ag_' + 'a' * 20, b'', stamp=1794560000, nonce='fedcba9876543210fedcba9876543210')
        self.assertEqual(b['X-Hence-Agent-Signature'], '9332bf9cdedc4a090a0252a50ee5a1f017f7b845aec5613ecaaf377b83e4f998')
        self.assertEqual((b['X-Hence-Agent-Grant'], b['X-Hence-Agent-Time']), ('ag_' + 'a' * 20, '1794560000'))
        self.assertNotEqual(appauth.sign('k' * 40, 'GET', '/p', '', b'')['X-Hence-Agent-Nonce'], appauth.sign('k' * 40, 'GET', '/p', '', b'')['X-Hence-Agent-Nonce'])


class AppCalls(Env):
    """appauth.AppAuth against a recorded transport."""

    def make(self, answers):
        self.sent = []
        self.now = [1000.0]

        def send(url, method, headers, body, timeout):
            self.sent.append((url, method, headers, body))
            a = answers.pop(0)
            if isinstance(a, BaseException):
                raise a
            return a if len(a) == 3 else (a[0], a[1], {})

        return appauth.AppAuth(base='http://app.test', send=send, clock=lambda: self.now[0])

    def active(self, **over):
        return 200, json.dumps(dict({'active': True, 'grant': GRANT, 'scope': 'account:read', 'client_name': 'Example Agent', 'expires_in': 3000}, **over)).encode()

    def test_asking_who_a_token_is(self):
        auth = self.make([self.active()])
        self.assertEqual(auth.connection(TOKEN), {'grant': GRANT, 'scopes': ['account:read'], 'client_name': 'Example Agent'})
        url, method, headers, body = self.sent[0]
        self.assertEqual((url, method), ('http://app.test/api/agents/v1/introspect', 'POST'))
        self.assertEqual(json.loads(body), {'token': TOKEN, 'resource': 'https://mcp.hence.markets/mcp'})     # the audience travels with the question
        expected = appauth.sign(SECRET, 'POST', '/api/agents/v1/introspect', '', body, headers['X-Hence-Agent-Time'], headers['X-Hence-Agent-Nonce'])
        self.assertEqual(headers['X-Hence-Agent-Signature'], expected['X-Hence-Agent-Signature'])
        self.assertNotIn('Authorization', headers)

    def test_a_good_answer_is_remembered_briefly_and_forgettable(self):
        auth = self.make([self.active(), self.active(), self.active()])
        auth.connection(TOKEN)
        auth.connection(TOKEN)
        self.assertEqual(len(self.sent), 1)
        self.now[0] += appauth.CACHE_S + 1
        auth.connection(TOKEN)
        self.assertEqual(len(self.sent), 2)
        auth.forget(TOKEN)
        auth.connection(TOKEN)
        self.assertEqual(len(self.sent), 3)
        self.assertNotIn(TOKEN, repr(auth._cache))                                     # remembered by digest, never by value

    def test_a_token_about_to_expire_is_not_remembered_past_it(self):
        auth = self.make([self.active(expires_in=5), self.active(expires_in=0), self.active(expires_in=0)])
        auth.connection(TOKEN)
        self.now[0] += 6
        auth.connection(TOKEN)
        auth.connection(TOKEN)
        self.assertEqual(len(self.sent), 3)

    def test_inactive_is_not_connected_and_everything_else_is_unavailable(self):
        with self.assertRaises(appauth.NotConnected):
            self.make([(200, b'{"active": false}')]).connection(TOKEN)
        for answer in ((401, b'{"error":"invalid_gateway"}'), (503, b'{}'), (200, b'<html>'), (200, b'{"active": true}'), (200, b'[]'),
                       TimeoutError('slow'), ConnectionRefusedError()):
            with self.assertRaises(appauth.Unavailable, msg=repr(answer)):
                self.make([answer]).connection(TOKEN)
        for junk in ('', None, 5, 'x' * 300):
            auth = self.make([])
            with self.assertRaises(appauth.NotConnected, msg=repr(junk)):
                auth.connection(junk)
            self.assertEqual(self.sent, [])

    def test_a_failure_is_never_remembered(self):
        auth = self.make([(503, b'{}'), self.active()])
        with self.assertRaises(appauth.Unavailable):
            auth.connection(TOKEN)
        self.assertEqual(auth.connection(TOKEN)['grant'], GRANT)

    def test_reading_under_a_connection_is_signed_and_carries_no_token(self):
        auth = self.make([(200, json.dumps(WHOAMI).encode())])
        self.assertEqual(auth.get(GRANT, '/api/agents/v1/whoami'), WHOAMI)
        url, method, headers, body = self.sent[0]
        self.assertEqual((url, method, body, headers['X-Hence-Agent-Grant']), ('http://app.test/api/agents/v1/whoami', 'GET', None, GRANT))
        self.assertNotIn('Authorization', headers)
        self.assertNotIn(TOKEN, json.dumps(headers))

    def test_each_refusal_of_a_read(self):
        for answer, exc in (((401, b'{"error":"invalid_token"}'), appauth.NotConnected), ((403, b'{"error":"insufficient_scope","scope":"account:read"}'), appauth.MissingScope),
                            ((429, b'{"error":"rate_limited"}', {'Retry-After': '9'}), appauth.Busy), ((401, b'{"error":"invalid_gateway"}'), appauth.Unavailable),
                            ((503, b'{}'), appauth.Unavailable), ((200, b'not json'), appauth.Unavailable), (OSError('down'), appauth.Unavailable)):
            with self.assertRaises(exc, msg=repr(answer)):
                self.make([answer]).get(GRANT, '/api/agents/v1/whoami')
        with self.assertRaises(appauth.Busy) as ctx:
            self.make([(429, b'{}', {'retry-after': '9'})]).get(GRANT, '/api/agents/v1/whoami')
        self.assertEqual(ctx.exception.retry_after, '9')

    def test_only_listed_paths_can_be_read(self):
        auth = self.make([])
        for path in ('/api/me', '/api/agents/v1/other', '/api/exchange', ''):
            with self.assertRaises(ValueError, msg=path):
                auth.get(GRANT, path)
        self.assertEqual(self.sent, [])

    def test_no_secret_means_no_calls(self):
        with mock.patch.dict(os.environ, {'HENCE_MCP_GATEWAY_SECRET': ''}):
            auth = self.make([])
            with self.assertRaises(appauth.Unavailable):
                auth.connection(TOKEN)
            self.assertEqual(self.sent, [])


class Served(Env):
    def setUp(self):
        super().setUp()
        self.auth = StubAuth()
        self.server = app.make_server('127.0.0.1', 0, upstream=self.up, limiter=RateLimiter(), auth=self.auth)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            r = conn.getresponse()
            data = r.read()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, (json.loads(data) if data else None)
        finally:
            conn.close()

    def test_the_metadata_says_who_signs_people_in(self):
        for path in ('/.well-known/oauth-protected-resource', '/.well-known/oauth-protected-resource/mcp'):
            status, headers, body = self.request('GET', path)
            self.assertEqual(status, 200, path)
            self.assertEqual(body, {'resource': 'https://mcp.hence.markets/mcp', 'authorization_servers': ['https://app.hence.markets'],
                                    'scopes_supported': ['account:read', 'portfolio:read', 'theses:read', 'theses:write'], 'bearer_methods_supported': ['header'], 'resource_name': 'Hence'})
            self.assertEqual(headers['cache-control'], 'public, max-age=300')
        self.assertEqual(self.request('GET', '/.well-known/oauth-protected-resource/other')[0], 404)
        with mock.patch.dict(os.environ, {'HENCE_MCP_ACCOUNTS': '0'}):
            self.assertEqual(self.request('GET', '/.well-known/oauth-protected-resource')[0], 404)        # no sign-in advertised while off

    def test_the_challenge_and_the_answer_over_http(self):
        headers, body = call_account()
        status, response, _ = self.request('POST', '/mcp', body, dict(headers, **{'Content-Type': 'application/json'}))
        self.assertEqual(status, 401)
        self.assertIn('resource_metadata="https://mcp.hence.markets/.well-known/oauth-protected-resource/mcp"', response['www-authenticate'])
        headers, body = call_account({'Authorization': 'Bearer ' + TOKEN})
        status, response, out = self.request('POST', '/mcp', body, dict(headers, **{'Content-Type': 'application/json'}))
        self.assertEqual((status, out['result']['structuredContent']['username']), (200, 'alice_trades'))
        self.assertNotIn('www-authenticate', response)
        self.assertEqual(response['cache-control'], 'no-store')


if __name__ == '__main__':
    unittest.main()
