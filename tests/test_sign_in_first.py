"""Sign-in first: a person connects their Hence account before this server answers anything.

Why it exists (decided 2026-11-23). This server is added by pasting its address, so whoever
adds it already wants it; a sign-in asked once, when it is added, is the step every client
handles, and a sign-in asked in the middle of a chat is not (Claude Code and Codex show the
model an error and no button). Signed out, Hence also cannot tell one person's use from
another's.

What these pin:
  * it is off unless switched on, and never on without sign-in configured: everything in
    tests/test_account.py and tests/test_protocol.py is how the server behaves with it off;
  * switched on, the hello, the tool list, the cards and a tool that reads PUBLIC data all
    answer the sign-in challenge to a caller with no credential, and the challenge asks for
    every permission at once (two of the clients never come back for a missing one);
  * what a person needs to find the sign-in stays open: the health check and the metadata;
  * "Hence did not answer the check" is never "sign in again", and never runs a tool;
  * a permission the app says this person cannot give is a sentence, not a challenge that
    would loop them through a consent screen, and its tools are not listed to them at all.
"""
import http.client
import json
import os
import threading
import unittest
from unittest import mock

from gateway import app, appauth, config, protocol, tools
from gateway.limits import RateLimiter

from .support import StubUpstream, standard_routes
from .test_account import GRANT, ON, TOKEN, StubAuth, modern

FIRST = dict(ON, HENCE_MCP_SIGN_IN_FIRST='1')
BEARER = {'authorization': 'Bearer ' + TOKEN}
EVERY_SCOPE = 'account:read theses:read portfolio:read theses:write'
METADATA = 'https://mcp.hence.markets/.well-known/oauth-protected-resource/mcp'
READS_AND_DRAFTS = ['account:read', 'theses:read', 'theses:write', 'portfolio:read']


def legacy(method, params=None, headers=None, msg_id=1):
    body = {'jsonrpc': '2.0', 'method': method}
    if params is not None:
        body['params'] = params
    if msg_id is not None:
        body['id'] = msg_id
    return dict(headers or {}), json.dumps(body).encode()


def call(name, arguments=None, headers=None):
    return modern('tools/call', {'name': name, 'arguments': arguments or {}}, headers, name)


def connection(scopes=('account:read',), grantable=None):
    return {'grant': GRANT, 'scopes': list(scopes), 'client_name': 'Example Agent', 'grantable': grantable}


class Env(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, FIRST)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.up = StubUpstream(standard_routes())

    def run_(self, request, auth=None):
        return protocol.handle_full(*request, self.up, auth=auth or StubAuth())


class TheSwitch(unittest.TestCase):
    def test_it_is_off_by_default_and_needs_sign_in_configured(self):
        with mock.patch.dict(os.environ, ON):
            os.environ.pop('HENCE_MCP_SIGN_IN_FIRST', None)
            self.assertFalse(config.sign_in_first())
        for change in ({}, {'HENCE_MCP_SIGN_IN_FIRST': '1'}):
            with mock.patch.dict(os.environ, dict(FIRST, **change)):
                self.assertTrue(config.sign_in_first())
        # strictly '1'; and never when the server could not complete the sign-in it would ask for
        for change in ({'HENCE_MCP_SIGN_IN_FIRST': 'true'}, {'HENCE_MCP_SIGN_IN_FIRST': '0'}, {'HENCE_MCP_ACCOUNTS': '0'},
                       {'HENCE_MCP_GATEWAY_SECRET': ''}, {'HENCE_MCP_GATEWAY_SECRET': 'x' * 31}):
            with mock.patch.dict(os.environ, dict(FIRST, **change)):
                self.assertFalse(config.sign_in_first(), change)

    def test_switched_off_a_public_tool_still_runs_signed_out(self):
        with mock.patch.dict(os.environ, ON):
            os.environ.pop('HENCE_MCP_SIGN_IN_FIRST', None)
            auth = StubAuth()
            status, out, _ = protocol.handle_full(*call('get_thesis_guide'), StubUpstream(standard_routes()), auth=auth)
            self.assertEqual((status, out['result']['isError']), (200, False))
            self.assertEqual(auth.asked, [])                                    # and never looks at a credential
            _, listed, _ = protocol.handle_full(*modern('tools/list'), StubUpstream(standard_routes()), auth=auth)
            self.assertEqual(listed['result']['cacheScope'], 'public')
            guide = next(t for t in listed['result']['tools'] if t['name'] == 'get_thesis_guide')
            self.assertEqual(guide['securitySchemes'], [{'type': 'noauth'}])


class NothingSignedOut(Env):
    def assert_asks_to_connect(self, answer, error=None):
        status, out, headers = answer
        self.assertEqual(status, 401)
        expected = 'Bearer %sresource_metadata="%s", scope="%s"' % ('error="%s", ' % error if error else '', METADATA, EVERY_SCOPE)
        self.assertEqual(headers['WWW-Authenticate'], expected)
        return out

    def test_every_request_asks_a_caller_with_no_credential_to_connect(self):
        auth = StubAuth()
        requests = [modern('server/discover'), modern('tools/list'), modern('resources/list'),
                    call('get_thesis_guide'), call('search_markets', {'query': 'bitcoin'}), call('get_my_account'),
                    call('no_such_tool'),                                  # which tools exist is not said either
                    legacy('initialize', {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 't', 'version': '0'}}),
                    legacy('tools/list'), legacy('ping'), legacy('tools/call', {'name': 'get_thesis_guide', 'arguments': {}})]
        for request in requests:
            out = self.assert_asks_to_connect(self.run_(request, auth))
            self.assertEqual(out['error'], {'code': protocol.SIGN_IN, 'message': 'Connect your Hence account to use Hence.'}, request[1])
        self.assertEqual((auth.asked, auth.read, self.up.calls), ([], [], []))          # nothing was read for anyone

    def test_a_notification_gets_the_challenge_and_no_body(self):
        status, out, headers = self.run_(legacy('notifications/initialized', msg_id=None))
        self.assertEqual((status, out), (401, None))
        self.assertIn('resource_metadata=', headers['WWW-Authenticate'])

    def test_the_challenge_asks_for_every_permission_the_tools_offered_need(self):
        self.assertEqual(protocol._all_scopes(), EVERY_SCOPE)
        with mock.patch.dict(os.environ, {'HENCE_MCP_TRADES': '1', 'HENCE_MCP_TRADING': '1'}):
            self.assertEqual(protocol._all_scopes(), EVERY_SCOPE + ' trades:propose trades:place')
            # the same permissions the metadata names, so a client that reads either asks for the same thing
            self.assertEqual(sorted(protocol._all_scopes().split()), protocol.resource_metadata()['scopes_supported'])

    def test_a_dead_credential_is_told_to_connect_again(self):
        auth = StubAuth(connection=appauth.NotConnected())
        for request in (modern('tools/list'), call('get_thesis_guide')):
            out = self.assert_asks_to_connect(self.run_((dict(request[0], **BEARER), request[1]), auth), error='invalid_token')
            self.assertIn('Connect it again', out['error']['message'])
        self.assertEqual(self.up.calls, [])

    def test_chatgpt_gets_the_challenge_as_a_tool_result_it_can_link_from(self):
        request = call('get_thesis_guide', headers={'user-agent': 'openai-mcp/1.0.0'})
        status, out, headers = self.run_(request)
        self.assertEqual((status, headers), (200, {}))
        self.assertTrue(out['result']['isError'])
        challenge = out['result']['_meta']['mcp/www_authenticate'][0]
        self.assertIn('error="insufficient_scope"', challenge)
        self.assertIn('scope="%s"' % EVERY_SCOPE, challenge)
        # its hello and its tool list are the HTTP 401 every client reads when a server is added
        self.assertEqual(self.run_(modern('tools/list', headers={'user-agent': 'openai-mcp/1.0.0'}))[0], 401)


class Connected(Env):
    def test_a_connected_person_gets_the_hello_the_list_and_a_public_tool(self):
        auth = StubAuth(connection=connection())
        for request in (modern('server/discover', headers=BEARER), modern('tools/list', headers=BEARER),
                        legacy('initialize', {'protocolVersion': '2025-11-25'}, BEARER), legacy('ping', headers=BEARER)):
            status, out, _ = self.run_(request, auth)
            self.assertEqual((status, 'result' in out), (200, True), request[1])
        status, out, _ = self.run_(call('get_thesis_guide', headers=BEARER), auth)
        self.assertEqual((status, out['result']['isError']), (200, False))
        self.assertEqual(out['result']['structuredContent']['format'], 'hence-thesis/1')
        self.assertEqual(set(auth.asked), {TOKEN})                     # every one of them was checked against the app
        self.assertEqual(auth.read, [])                                 # and a public tool reads nothing of the person's

    def test_the_list_is_the_persons_own_and_never_shared(self):
        _, out, _ = self.run_(modern('tools/list', headers=BEARER), StubAuth(connection=connection()))
        self.assertEqual(out['result']['cacheScope'], 'private')
        guide = next(t for t in out['result']['tools'] if t['name'] == 'get_thesis_guide')
        self.assertEqual(guide['securitySchemes'], [{'type': 'oauth2', 'scopes': []}])       # no tool is declared as running signed out
        self.assertEqual(guide['_meta']['securitySchemes'], [{'type': 'oauth2', 'scopes': []}])

    def test_the_model_is_told_nothing_here_runs_signed_out(self):
        _, out, _ = self.run_(modern('server/discover', headers=BEARER), StubAuth(connection=connection()))
        said = out['result']['instructions']
        self.assertIn('Every tool here runs under the person\'s connected Hence account', said)
        self.assertNotIn('need no sign-in', said)
        self.assertIn('never ask for the code', said)
        with mock.patch.dict(os.environ, {'HENCE_MCP_SIGN_IN_FIRST': '0'}):
            self.assertIn('Public theses and markets need no sign-in.', protocol.instructions())


class HenceDidNotAnswerTheCheck(Env):
    """A credential was sent and the app did not say whether it is good. Not "connected", and
    not "sign in again": that would make a client throw away a good connection every time the
    app restarts, which it does on every release."""

    def setUp(self):
        super().setUp()
        self.auth = StubAuth(connection=appauth.Unavailable('Hence could not be reached (TimeoutError)'))

    def test_the_hello_and_the_list_are_still_answered(self):
        for request in (modern('server/discover', headers=BEARER), modern('tools/list', headers=BEARER),
                        legacy('initialize', {'protocolVersion': '2025-11-25'}, BEARER)):
            status, out, headers = self.run_(request, self.auth)
            self.assertEqual((status, 'result' in out, headers), (200, True, {}), request[1])

    def test_no_tool_runs_and_nobody_is_told_to_sign_in_again(self):
        for name in ('get_thesis_guide', 'get_my_account'):
            status, out, headers = self.run_(call(name, headers=BEARER), self.auth)
            self.assertEqual((status, headers), (200, {}), name)
            self.assertTrue(out['result']['isError'], name)
            text = out['result']['content'][0]['text']
            self.assertIn('Nothing was read or done; try again shortly.', text)
            self.assertNotIn('sign in', text.lower())
        status, out, _ = self.run_(call('search_markets', {'query': 'bitcoin'}, BEARER), self.auth)
        self.assertTrue(out['result']['isError'])
        self.assertEqual((self.up.calls, self.auth.read, self.auth.forgotten), ([], [], []))


class NotOpenToThisPerson(unittest.TestCase):
    """The app opens some permissions to some accounts only (trading, to the team). It says
    which a person could give in `grantable`; a missing permission outside it can never be
    granted, so asking for it again would send them round the consent screen for ever."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, dict(FIRST, HENCE_MCP_TRADES='1', HENCE_MCP_TRADING='1'))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.up = StubUpstream(standard_routes())
        self.auth = StubAuth(connection=connection(READS_AND_DRAFTS, READS_AND_DRAFTS))

    def names(self, auth, headers=BEARER, request=modern):
        _, out, _ = protocol.handle_full(*request('tools/list', headers=headers), self.up, auth=auth)
        return [t['name'] for t in out['result']['tools']]

    def test_their_list_leaves_out_what_they_could_never_be_given(self):
        closed = {'propose_trade', 'propose_basket', 'get_trade_proposals', 'get_trading_status', 'place_trade'}
        everything = [t['name'] for t in tools.listed()]
        self.assertTrue(closed <= set(everything))
        for request in (modern, legacy):
            self.assertEqual(self.names(self.auth, request=request), [n for n in everything if n not in closed])
        # someone the app would give them to sees them, granted yet or not
        team = StubAuth(connection=connection(READS_AND_DRAFTS, READS_AND_DRAFTS + ['trades:propose', 'trades:place']))
        self.assertEqual(self.names(team), everything)
        # an app that does not say what is open to them: nothing is assumed closed
        self.assertEqual(self.names(StubAuth(connection=connection(READS_AND_DRAFTS, None))), everything)

    def test_calling_one_anyway_is_a_sentence_not_a_challenge(self):
        lines = []
        status, out, headers = protocol.handle_full(*call('get_trade_proposals', headers=BEARER), self.up, log=lines.append, auth=self.auth)
        self.assertEqual((status, headers), (200, {}))
        self.assertTrue(out['result']['isError'])
        self.assertEqual(out['result']['content'][0]['text'], protocol.NOT_OPEN_TO_YOU)
        self.assertEqual(lines, ['tool=get_trade_proposals outcome=not-open'])
        self.assertEqual(self.auth.read, [])

    def test_a_permission_they_could_give_and_have_not_is_still_asked_for(self):
        auth = StubAuth(connection=connection(['account:read'], READS_AND_DRAFTS))
        status, _, headers = protocol.handle_full(*call('get_my_portfolio', headers=BEARER), self.up, auth=auth)
        self.assertEqual(status, 403)
        self.assertIn('error="insufficient_scope"', headers['WWW-Authenticate'])
        self.assertIn('scope="portfolio:read"', headers['WWW-Authenticate'])

    def test_the_same_holds_with_the_switch_off(self):
        # the sentence is about the person's account, not about sign-in first: a server that still
        # answers signed out must not loop a connected person through consent either
        with mock.patch.dict(os.environ, {'HENCE_MCP_SIGN_IN_FIRST': '0'}):
            status, out, headers = protocol.handle_full(*call('get_trade_proposals', headers=BEARER), self.up, auth=self.auth)
            self.assertEqual((status, headers, out['result']['content'][0]['text']), (200, {}, protocol.NOT_OPEN_TO_YOU))


class WhatTheAppSaid(unittest.TestCase):
    def test_grantable_is_read_from_the_app_and_absent_is_unknown(self):
        def auth(answer):
            body = json.dumps(dict({'active': True, 'grant': GRANT, 'scope': 'account:read theses:read', 'client_name': 'x', 'expires_in': 3000}, **answer)).encode()
            return appauth.AppAuth(base='http://app.test', send=lambda *a: (200, body, {}), clock=lambda: 1000.0)

        with mock.patch.dict(os.environ, FIRST):
            self.assertEqual(auth({'grantable': 'account:read theses:read theses:write'}).connection(TOKEN)['grantable'],
                             ['account:read', 'theses:read', 'theses:write'])
            self.assertEqual(auth({'grantable': ''}).connection(TOKEN)['grantable'], [])        # said, and nothing is open
            for unsaid in ({}, {'grantable': None}, {'grantable': ['account:read']}, {'grantable': 7}):
                self.assertIsNone(auth(unsaid).connection(TOKEN)['grantable'], unsaid)       # not said, or not readable: unknown


class Served(Env):
    """Over real HTTP: what a person needs to FIND the sign-in stays open."""

    def setUp(self):
        super().setUp()
        self.auth = StubAuth(connection=connection())
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

    def post(self, request, bearer=False):
        headers, body = request
        headers = dict(headers, **{'Content-Type': 'application/json'})
        if bearer:
            headers['Authorization'] = 'Bearer ' + TOKEN
        return self.request('POST', '/mcp', body, headers)

    def test_the_health_check_and_the_metadata_need_no_sign_in(self):
        self.assertEqual(self.request('GET', '/healthz')[0], 200)
        status, _, body = self.request('GET', '/.well-known/oauth-protected-resource/mcp')
        self.assertEqual((status, body['authorization_servers']), (200, ['https://app.hence.markets']))

    def test_adding_the_server_meets_the_sign_in_and_connecting_opens_it(self):
        hello = legacy('initialize', {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 't', 'version': '0'}})
        status, headers, body = self.post(hello)
        self.assertEqual(status, 401)
        self.assertEqual(headers['www-authenticate'], 'Bearer resource_metadata="%s", scope="%s"' % (METADATA, EVERY_SCOPE))
        self.assertEqual(body['error']['code'], protocol.SIGN_IN)
        status, _, body = self.post(hello, bearer=True)
        self.assertEqual((status, body['result']['serverInfo']['name']), (200, 'hence'))
        status, _, body = self.post(call('get_thesis_guide'), bearer=True)
        self.assertEqual((status, body['result']['isError']), (200, False))


if __name__ == '__main__':
    unittest.main()
