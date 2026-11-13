"""The `hence` command. No network: a stand-in answers every address the command may call.

What these pin:
  * a failure is a sentence on stderr and a non-zero exit, with stdout empty: a script piping
    the command can never read an error as data;
  * a figure that was not read prints as "n/a", never as zero, and nothing the server said is
    dropped;
  * the credential goes only to the server it was issued for, only when that server asks for
    it, and is never printed;
  * sign-in refuses every answer it cannot tie to this sign-in (state, issuer, PKCE, https).
"""
import base64
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import unittest
import unittest.mock
import urllib.parse
import urllib.request

from gateway import tools
from hence_cli import auth, client, main, render
from hence_cli.store import Store

from .support import T1, StubUpstream, standard_routes

SERVER = 'https://mcp.hence.markets/mcp'
ISSUER = 'https://app.hence.markets'
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'hence_cli')

MARKETS = {'markets': [
    {'symbol': 'NVDA', 'kind': 'perp', 'venue': 'trade.xyz', 'price': 168.96, 'change_24h_pct': 6.5, 'funding_apr_pct': 10.4,
     'open_interest_usd': 366800000.0, 'volume_24h_usd': 1850000000.0, 'url': 'https://app.hence.markets/m/xyz/xyz%3ANVDA'},
    {'symbol': 'NVDA', 'kind': 'spot', 'venue': 'Robinhood Chain', 'price': None, 'change_24h_pct': None, 'funding_apr_pct': None,
     'open_interest_usd': None, 'volume_24h_usd': None, 'url': 'https://app.hence.markets/m/rhc/0xabc'}],
    'matches': 2, 'unavailable': {'spot': 'Hence could not be reached'}}


class _App:
    """What the Hence app answers the server's whoami read."""

    def __init__(self, handle):
        self.handle = handle

    def get(self, path):
        return {'handle': self.handle, 'profile_url': 'https://app.hence.markets/u/%s' % self.handle if self.handle else None,
                'agent': 'Hence CLI', 'scopes': ['account:read']}


def real_account_answer(handle):
    """The account tool's answer, made by the SERVER'S OWN function. The command's views are
    tested against what the server really sends, so the two cannot drift apart unnoticed (a
    view reading a field the server does not send once said "no username" for an account
    that had one)."""
    return tools.get_my_account({}, None, _App(handle))


class Hence:
    """Stands in for the MCP server and for Hence's sign-in service. Records every request."""

    def __init__(self):
        self.requests = []
        self.accounts = True                  # the server offers sign-in
        self.good = {'hat_first'}             # access tokens it accepts
        self.refresh = {'hrt_first': ('hat_second', 'hrt_second')}
        self.codes = {}                       # code -> (challenge, redirect_uri)
        self.revoked = []
        self.refresh_status = None            # force the token endpoint's answer for a refresh
        self.tool_error = None
        self.meta_resource = SERVER
        self.meta_issuer = ISSUER
        self.token_endpoint = ISSUER + '/api/oauth/token'

    def __call__(self, url, method, headers, body, timeout):
        self.requests.append((method, url, dict(headers), body))
        u = urllib.parse.urlsplit(url)
        if url == SERVER:
            return self._mcp(headers, json.loads(body))
        if u.path.startswith('/.well-known/oauth-protected-resource'):
            if not self.accounts:
                return 404, b'', {}
            return self._json(200, {'resource': self.meta_resource, 'authorization_servers': [ISSUER], 'scopes_supported': ['account:read']})
        if u.path == '/.well-known/oauth-authorization-server':
            return self._json(200, {'issuer': self.meta_issuer, 'authorization_endpoint': ISSUER + '/oauth/authorize',
                                    'token_endpoint': self.token_endpoint, 'registration_endpoint': ISSUER + '/api/oauth/register',
                                    'revocation_endpoint': ISSUER + '/api/oauth/revoke', 'code_challenge_methods_supported': ['S256'],
                                    'authorization_response_iss_parameter_supported': True})
        if u.path == '/api/oauth/register':
            return self._json(201, {'client_id': 'hc_cli'})
        if u.path == '/api/oauth/revoke':
            self.revoked.append(dict(urllib.parse.parse_qsl(body.decode()))['token'])
            return self._json(200, {})
        if u.path == '/api/oauth/token':
            f = dict(urllib.parse.parse_qsl(body.decode()))
            if f.get('grant_type') == 'authorization_code':
                challenge, redirect = self.codes.pop(f.get('code'), (None, None))
                made = base64.urlsafe_b64encode(hashlib.sha256(f.get('code_verifier', '').encode()).digest()).decode().rstrip('=')
                if challenge is None or made != challenge or redirect != f.get('redirect_uri') or f.get('resource') != SERVER:
                    return self._json(400, {'error': 'invalid_grant'})
                return self._json(200, {'access_token': 'hat_first', 'refresh_token': 'hrt_first', 'expires_in': 3600, 'scope': 'account:read'})
            if self.refresh_status:
                return self._json(self.refresh_status, {'error': 'temporarily_unavailable'})
            pair = self.refresh.pop(f.get('refresh_token'), None)
            if not pair:
                return self._json(400, {'error': 'invalid_grant'})
            self.good = {pair[0]}
            return self._json(200, {'access_token': pair[0], 'refresh_token': pair[1], 'expires_in': 3600, 'scope': 'account:read'})
        raise AssertionError('unexpected request: %s %s' % (method, url))

    @staticmethod
    def _json(status, obj, headers=None):
        return status, json.dumps(obj).encode(), dict(headers or {})

    def _mcp(self, headers, msg):
        if msg['method'] == 'tools/list':
            return self._json(200, {'jsonrpc': '2.0', 'id': 1, 'result': {'tools': [{'name': 'search_markets', 'title': 'Search Hence markets'}]}})
        name = msg['params']['name']
        if name == 'get_my_account':
            token = headers.get('Authorization', '')[7:]
            if token not in self.good:
                return self._json(401, {'jsonrpc': '2.0', 'id': 1, 'error': {'code': -32001, 'message': 'Sign in to Hence to use this.'}},
                                  {'www-authenticate': 'Bearer resource_metadata="x", scope="account:read"'})
            return self._ok(real_account_answer('ivan'))
        if self.tool_error:
            return self._json(200, {'jsonrpc': '2.0', 'id': 1, 'result': {'content': [{'type': 'text', 'text': self.tool_error}], 'isError': True}})
        return self._ok(MARKETS)

    def _ok(self, value):
        return self._json(200, {'jsonrpc': '2.0', 'id': 1, 'result': {'content': [{'type': 'text', 'text': json.dumps(value)}],
                                                                       'structuredContent': value, 'isError': False}})

    def browser(self, change=None):
        """A person pressing Allow: follows the sign-in link and lands on the command's port."""
        def open_browser(url):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
            self.authorize = q
            self.codes['hac_code'] = (q['code_challenge'], q['redirect_uri'])
            back = {'code': 'hac_code', 'state': q['state'], 'iss': ISSUER}
            if change:
                change(back)
            try:
                urllib.request.urlopen(q['redirect_uri'] + '?' + urllib.parse.urlencode(back), timeout=5).read()
            except Exception:
                pass
        return open_browser

    def sent_tokens(self, url=SERVER):
        return [h.get('Authorization') for m, u, h, b in self.requests if u == url]


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = Store(os.path.join(self.dir.name, 'hence', 'credentials.json'))
        self.hence = Hence()

    def run_cli(self, *argv, browser=None, server=SERVER):
        out, err = io.StringIO(), io.StringIO()
        code = main.run(['--server', server] + list(argv), out=out, err=err, store=self.store, sender=self.hence, open_browser=browser)
        return code, out.getvalue(), err.getvalue()

    def login(self):
        code, out, err = self.run_cli('login', browser=self.hence.browser())
        self.assertEqual(code, 0, err)
        return out, err


class Reading(Case):
    def test_a_public_tool_prints_a_table_and_never_sends_a_credential(self):
        self.login()
        code, out, err = self.run_cli('markets', 'nvda')
        self.assertEqual((code, err), (0, ''))
        self.assertIn('trade.xyz', out)
        self.assertEqual(self.hence.sent_tokens()[-1], None)          # signed in, and still not sent

    def test_a_figure_that_was_not_read_is_not_a_zero(self):
        code, out, _ = self.run_cli('markets', 'nvda')
        spot = [line for line in out.splitlines() if 'Robinhood Chain' in line][0]
        self.assertEqual(spot.count('n/a'), 5)
        self.assertNotRegex(spot, r'\$0|\b0\.00\b|\+0\.00%')

    def test_nothing_the_server_said_is_dropped(self):
        code, out, _ = self.run_cli('markets', 'nvda')
        self.assertIn('unavailable: {"spot": "Hence could not be reached"}', out)
        self.assertIn('matches: 2', out)

    def test_json_is_the_exact_answer_wherever_the_flag_is_typed(self):
        for argv in (['--json', 'markets', 'nvda'], ['markets', 'nvda', '--json']):
            code, out, _ = self.run_cli(*argv)
            self.assertEqual(json.loads(out), MARKETS, argv)

    def test_a_tool_that_could_not_do_it_is_stderr_and_exit_1_with_stdout_empty(self):
        self.hence.tool_error = 'Hence could not be reached. Nothing was read; try again shortly.'
        for argv in (['markets', 'nvda'], ['markets', 'nvda', '--json']):
            code, out, err = self.run_cli(*argv)
            self.assertEqual((code, out), (1, ''), argv)
            self.assertIn('Nothing was read', err)

    def test_an_unreachable_server_is_stderr_and_exit_1_with_stdout_empty(self):
        def dead(*a):
            raise client.Unreachable('Hence could not be reached (URLError). Nothing was read.')
        out, err = io.StringIO(), io.StringIO()
        code = main.run(['markets', 'btc'], out=out, err=err, store=self.store, sender=dead)
        self.assertEqual((code, out.getvalue()), (1, ''))
        self.assertIn('could not be reached', err.getvalue())

    def test_an_answer_without_structure_is_not_printed_as_if_it_were_one(self):
        self.hence._ok = lambda value: Hence._json(200, {'jsonrpc': '2.0', 'id': 1, 'result': {'content': [], 'isError': False}})
        code, out, err = self.run_cli('markets', 'nvda')
        self.assertEqual((code, out), (1, ''))

    def test_any_tool_can_be_called_by_name(self):
        code, out, _ = self.run_cli('call', 'search_markets', '--arg', 'query=nvda', '--arg', 'limit=3', '--args', '{"kind": "perp"}')
        self.assertEqual(code, 0)
        sent = json.loads(self.hence.requests[-1][3])['params']
        self.assertEqual(sent['arguments'], {'kind': 'perp', 'query': 'nvda', 'limit': 3})
        self.assertEqual(self.hence.requests[-1][2]['Mcp-Name'], 'search_markets')

    def test_a_thesis_is_named_by_id_or_by_its_link(self):
        self.run_cli('thesis', 'https://app.hence.markets/t/E6E70C06-6211-4956-8E0C-806342E0738F?x=1')
        self.assertEqual(json.loads(self.hence.requests[-1][3])['params']['arguments'], {'thesis_id': 'e6e70c06-6211-4956-8e0c-806342e0738f'})
        code, out, err = self.run_cli('thesis', 'nope')
        self.assertEqual((code, out), (1, ''))

    def test_only_https_or_this_machine(self):
        for bad in ('http://example.com/mcp', 'ftp://mcp.hence.markets/mcp', 'https://user:pw@mcp.hence.markets/mcp', 'mcp.hence.markets'):
            with self.assertRaises(client.CliError, msg=bad):
                client.check_server(bad)
        self.assertEqual(client.check_server('http://127.0.0.1:4340/mcp/'), 'http://127.0.0.1:4340/mcp')


class SigningIn(Case):
    def test_login_stores_the_sign_in_in_a_file_only_its_owner_can_read(self):
        out, err = self.login()
        self.assertIn('Connected.', out)
        self.assertEqual(stat.S_IMODE(os.stat(self.store.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.store.path)).st_mode), 0o700)
        self.assertEqual(self.store.access_token(SERVER), 'hat_first')
        q = self.hence.authorize
        self.assertEqual((q['response_type'], q['code_challenge_method'], q['resource'], q['scope']), ('code', 'S256', SERVER, 'account:read'))
        self.assertRegex(q['redirect_uri'], r'^http://127\.0\.0\.1:\d+/callback$')

    def test_no_credential_is_ever_printed(self):
        out, err = self.login()
        code, out2, err2 = self.run_cli('whoami')
        code, out3, err3 = self.run_cli('logout')
        everything = out + out2 + out3 + err2 + err3
        self.assertNotRegex(everything, r'hat_|hrt_|hac_')
        # the sign-in link is shown (the person may need to copy it); it carries no secret
        self.assertNotRegex(err, r'hat_|hrt_|hac_|code_verifier')

    def test_whoami_sends_the_credential_only_after_the_server_asks(self):
        self.login()
        code, out, err = self.run_cli('whoami')
        self.assertEqual(code, 0, err)
        self.assertIn('Signed in as @ivan', out)
        self.assertEqual(self.hence.sent_tokens()[-2:], [None, 'Bearer hat_first'])

    def test_an_expired_sign_in_is_renewed_once_and_the_new_pair_replaces_the_old(self):
        self.login()
        self.hence.good = set()                                # Hence no longer accepts the first token
        code, out, err = self.run_cli('whoami')
        self.assertEqual(code, 0, err)
        entry = self.store.get(SERVER)
        self.assertEqual((entry['access_token'], entry['refresh_token']), ('hat_second', 'hrt_second'))

    def test_a_sign_in_hence_ended_asks_the_person_to_sign_in_again(self):
        self.login()
        self.hence.good, self.hence.refresh = set(), {}
        code, out, err = self.run_cli('whoami')
        self.assertEqual((code, out), (3, ''))
        self.assertIn('hence login', err)
        self.assertIsNone(self.store.get(SERVER).get('refresh_token'))
        self.assertEqual(self.store.get(SERVER)['client_id'], 'hc_cli')     # the registration is kept

    def test_a_renewal_that_got_no_answer_keeps_the_sign_in(self):
        self.login()
        self.hence.good, self.hence.refresh_status = set(), 503
        code, out, err = self.run_cli('whoami')
        self.assertEqual((code, out), (1, ''))
        self.assertEqual(self.store.get(SERVER)['refresh_token'], 'hrt_first')

    def test_not_signed_in_is_exit_3(self):
        code, out, err = self.run_cli('whoami')
        self.assertEqual((code, out), (3, ''))
        self.assertIn('hence login', err)

    def test_a_credential_is_never_sent_to_another_server(self):
        self.login()
        other = 'https://other.example/mcp'
        seen = []

        def elsewhere(url, method, headers, body, timeout):
            seen.append(headers.get('Authorization'))
            return 401, json.dumps({'error': {'message': 'Sign in.'}}).encode(), {'www-authenticate': 'Bearer scope="account:read"'}
        out, err = io.StringIO(), io.StringIO()
        code = main.run(['--server', other, 'whoami'], out=out, err=err, store=self.store, sender=elsewhere)
        self.assertEqual(code, 3)
        self.assertEqual(seen, [None])

    def test_logout_tells_hence_and_forgets(self):
        self.login()
        code, out, err = self.run_cli('logout')
        self.assertEqual(code, 0)
        self.assertIn('Hence has closed the connection', out)
        self.assertEqual(self.hence.revoked, ['hrt_first'])
        self.assertIsNone(self.store.access_token(SERVER))
        code, out, err = self.run_cli('logout')
        self.assertIn('was not connected', out)

    def test_a_server_without_sign_in_says_so(self):
        self.hence.accounts = False
        code, out, err = self.run_cli('login', browser=self.hence.browser())
        self.assertEqual((code, out), (1, ''))
        self.assertIn('not accepting sign-ins yet', err)


class RefusedAnswers(Case):
    def refused(self, change=None, **setup):
        for k, v in setup.items():
            setattr(self.hence, k, v)
        code, out, err = self.run_cli('login', browser=self.hence.browser(change))
        self.assertEqual((code, out), (1, ''), err)
        self.assertIsNone(self.store.access_token(SERVER))
        return err

    def test_an_answer_for_another_sign_in_is_dropped(self):
        self.assertIn('did not match this sign-in', self.refused(lambda back: back.update(state='someone-elses')))

    def test_an_answer_from_another_service_is_dropped(self):
        self.assertIn("did not come from Hence's sign-in service", self.refused(lambda back: back.update(iss='https://evil.example')))
        self.assertIn("did not come from Hence's sign-in service", self.refused(lambda back: back.pop('iss')))

    def test_a_cancel_is_said_as_a_cancel(self):
        def cancel(back):
            back.pop('code')
            back['error'] = 'access_denied'
        self.assertIn('You cancelled in Hence', self.refused(cancel))

    def test_sign_in_details_about_another_server_are_refused(self):
        self.assertIn('do not match its address', self.refused(meta_resource='https://evil.example/mcp'))

    def test_a_sign_in_service_that_does_not_name_itself_is_refused(self):
        self.assertIn('did not confirm who it is', self.refused(meta_issuer='https://evil.example'))

    def test_a_sign_in_address_that_is_not_https_is_refused(self):
        self.assertIn('not on https', self.refused(token_endpoint='http://app.hence.markets/api/oauth/token'))

    def test_the_code_is_useless_without_the_secret_made_here(self):
        # the stand-in's token endpoint recomputes the challenge from the verifier it is sent
        self.login()
        sent = [dict(urllib.parse.parse_qsl(b.decode())) for m, u, h, b in self.hence.requests if u.endswith('/api/oauth/token')][0]
        made = base64.urlsafe_b64encode(hashlib.sha256(sent['code_verifier'].encode()).digest()).decode().rstrip('=')
        self.assertEqual(made, self.hence.authorize['code_challenge'])
        self.assertGreaterEqual(len(sent['code_verifier']), 43)


class Rendering(unittest.TestCase):
    def test_unread_figures(self):
        self.assertEqual((render.pct(None), render.money(None), render.price(None), render.text('')), ('n/a',) * 4)
        self.assertEqual((render.pct(0), render.pct(-1.234), render.money(1850000000.0), render.price(0.00001234)),
                         ('+0.00%', '-1.23%', '$1.85B', '1.234e-05'))
        self.assertEqual(render.pct(True), 'n/a')               # a boolean is not a figure
        self.assertEqual((render.count(4.0), render.count(3), render.count(None), render.count(True)), ('4', '3', 'n/a', 'n/a'))

    def test_an_incomplete_price_history_says_so(self):
        text = render.thesis({'title': 'T', 'author': '@a', 'topic': 'x', 'url': 'u', 'performance': {
            'period_days': 90, 'from': 'a', 'to': 'b', 'return_pct': None, 'benchmark': 'BTC', 'benchmark_return_pct': 1.0,
            'max_drawdown_pct': None, 'complete': False, 'holdings_left_out': 2}})
        self.assertIn('n/a', text)
        self.assertIn('incomplete: 2 holdings', text)
        self.assertIn('Price history: n/a', render.thesis({'title': 'T', 'url': 'u', 'performance': None}))

    def test_a_tool_newer_than_this_release_prints_whole(self):
        self.assertEqual(json.loads(render.show('some_new_tool', {'a': 1})), {'a': 1})


class ViewsMatchTheServer(unittest.TestCase):
    """Every view is fed the answer the server's own tool function produces from the test
    fixtures. A view that reads a field the server does not send shows up here as a missing
    fact, not later as a wrong sentence on someone's screen."""

    def setUp(self):
        self.up = StubUpstream(standard_routes())

    def test_every_tool_the_server_lists_has_a_view_or_prints_whole(self):
        for t in tools.TOOLS + tools.ACCOUNT_TOOLS:
            self.assertIn(t['name'], render.VIEWS, '%s has no view: add one, or accept JSON on purpose here' % t['name'])

    def test_account(self):
        text = render.account(real_account_answer('ivan'))
        self.assertIn('Signed in as @ivan', text)
        self.assertIn('https://app.hence.markets/u/ivan', text)
        self.assertIn('This connection may: account:read', text)
        none = render.account(real_account_answer(None))
        self.assertIn('Signed in to a Hence account', none)
        self.assertIn('has not claimed a username yet', none)       # the server's own note, printed as it came
        self.assertNotIn('@', none)

    def test_my_theses_thesis_and_portfolio(self):
        from .test_my_account import BASE, DETAIL, PERF, PORTFOLIO, ROW, App
        class Account:
            def __init__(self, app):
                self.app = app
            def get(self, path, query=None):
                return self.app.get('grant', path, query)
        app = App({BASE + '/theses': {'theses': [ROW], 'total': 5, 'snapshot': 's', 'next_offset': 1, 'unread': 2},
                   BASE + '/theses/' + T1: DETAIL, BASE + '/theses/%s/performance' % T1: PERF, BASE + '/portfolio': PORTFOLIO})
        listed = render.show('list_my_theses', tools.list_my_theses({}, None, Account(app)))
        for said in ('Overbuilt', '[private, active]', 'https://app.hence.markets/t/' + T1, '--cursor', 'not complete'):
            self.assertIn(said, listed)
        one = render.show('get_my_thesis', tools.get_my_thesis({'thesis_id': T1}, None, Account(app)))
        for said in ('[private, active]', 'Overbuilt', 'xyz:NVDA', '-6.86%'):
            self.assertIn(said, one)
        held = render.show('get_my_portfolio', tools.get_my_portfolio({}, None, Account(app)))
        for said in ('xyz:NVDA', 'short', 'not complete: could not read 0x2222…2222 on Entropy', '$12.50', '$4.20K', 'not the value now',
                     'Not included (this is not the whole portfolio):', '  - spot token balances'):
            self.assertIn(said, held)
        dark = dict(PORTFOLIO, positions=dict(PORTFOLIO['positions'], positions=None), history=None, last_recorded_value=None, unavailable=['positions', 'history'])
        unknown = render.show('get_my_portfolio', tools.get_my_portfolio({}, None, Account(App({BASE + '/portfolio': dark}))))
        self.assertIn('Open perpetual positions: n/a (they could not be read)', unknown)
        self.assertIn('Trading results: n/a (they could not be read)', unknown)
        self.assertNotIn('none', unknown.split('unavailable')[0])          # unknown is never printed as "none"
        empty = dict(PORTFOLIO, positions=dict(PORTFOLIO['positions'], positions=[], complete=True, unread=[]),
                     history=dict(PORTFOLIO['history'], has_history=False))
        nothing = render.show('get_my_portfolio', tools.get_my_portfolio({}, None, Account(App({BASE + '/portfolio': empty}))))
        self.assertIn('Open perpetual positions: none', nothing)
        self.assertIn('Trading results: nothing recorded yet', nothing)

    def test_search_and_featured(self):
        found = tools.search_theses({'query': ''}, self.up)
        text = render.show('search_theses', found)
        for t in found['theses']:
            self.assertIn(t['title'], text)
            self.assertIn(t['url'], text)
        featured = tools.get_featured_theses({}, self.up)
        text = render.show('get_featured_theses', featured)
        for t in featured['theses']:
            self.assertIn(t['title'], text)
            if t.get('return_1m_pct') is not None:
                self.assertIn(render.pct(t['return_1m_pct']), text)

    def test_thesis(self):
        value = tools.get_thesis({'thesis_id': T1}, self.up)
        text = render.show('get_thesis', value)
        self.assertIn(value['title'], text)
        self.assertIn(value['url'], text)
        for h in value['holdings']:
            self.assertIn(h['symbol'], text)
        if isinstance(value.get('performance'), dict) and value['performance'].get('return_pct') is not None:
            self.assertIn(render.pct(value['performance']['return_pct']), text)

    def test_markets(self):
        value = tools.search_markets({'query': ''}, self.up)
        text = render.show('search_markets', value)
        for m in value['markets']:
            self.assertIn(m['symbol'], text)
            if m.get('price') is not None:
                self.assertIn(render.price(m['price']), text)


class Packaging(unittest.TestCase):
    """The plugin files and the skill: what an agent installs. They must point at the same
    server the code does, and name each other consistently, or the install succeeds and then
    nothing works."""
    ROOT = os.path.join(SRC, '..')

    def read(self, rel):
        with open(os.path.join(self.ROOT, rel), encoding='utf-8') as f:
            return f.read()

    def test_the_plugin_and_its_marketplace_agree(self):
        plugin = json.loads(self.read('.claude-plugin/plugin.json'))
        market = json.loads(self.read('.claude-plugin/marketplace.json'))
        self.assertEqual([p['name'] for p in market['plugins']], [plugin['name']])
        self.assertEqual(market['plugins'][0]['source'], './')
        self.assertTrue(market['owner']['name'] and plugin['description'] and plugin['version'] and plugin['author']['name'])
        self.assertNotRegex(plugin['name'], r'^(claude|anthropic|cc-plugin)')

    def test_the_plugin_points_at_the_server_the_code_points_at(self):
        servers = json.loads(self.read('.mcp.json'))['mcpServers']
        self.assertEqual(servers, {'hence': {'type': 'http', 'url': client.DEFAULT_SERVER}})
        from gateway import config
        self.assertEqual(client.DEFAULT_SERVER, 'https://mcp.hence.markets/mcp')
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('HENCE_MCP_RESOURCE', None)
            self.assertEqual(config.resource(), client.DEFAULT_SERVER)

    def test_the_skill_names_only_tools_and_commands_that_exist(self):
        text = self.read('skills/hence/SKILL.md')
        front = text.split('---')[1]
        self.assertRegex(front, r'(?m)^name: hence$')
        description = re.search(r'(?m)^description: (.+)$', front).group(1)
        self.assertLessEqual(len(description), 1024)
        known = {t['name'] for t in tools.TOOLS + tools.ACCOUNT_TOOLS}
        for name in set(re.findall(r'`((?:search|get)_[a-z_]+)`', text)):
            self.assertIn(name, known, name)
        self.assertIn(client.DEFAULT_SERVER, text)
        sub = {a.dest: a for a in main._parser()._subparsers._group_actions}['command'].choices
        for command in set(re.findall(r'(?m)^hence ([a-z]+)', text)):
            self.assertIn(command, sub, command)


class Source(unittest.TestCase):
    def files(self):
        out = {}
        for name in sorted(os.listdir(SRC)):
            if name.endswith('.py'):
                with open(os.path.join(SRC, name), encoding='utf-8') as f:
                    out[name] = f.read()
        return out

    def test_standard_library_only(self):
        allowed = {'argparse', 'base64', 'hashlib', 'hmac', 'http', 'json', 'os', 're', 'secrets', 'sys', 'threading', 'time', 'urllib', 'webbrowser'}
        for name, text in self.files().items():
            for mod in re.findall(r'^\s*(?:import|from)\s+([A-Za-z_][\w]*)', text, re.M):
                self.assertIn(mod, allowed, '%s imports %s' % (name, mod))

    def test_only_the_command_prints_and_never_a_credential(self):
        for name, text in self.files().items():
            if name != 'main.py':
                self.assertNotRegex(text, r'^\s*print\(', name)
        for line in self.files()['main.py'].splitlines():
            if 'print(' in line:
                self.assertNotRegex(line, r'token|verifier|secret', line)

    def test_the_network_is_reached_in_one_place(self):
        hits = [name for name, text in self.files().items() if 'urlopen(' in text]
        self.assertEqual(hits, ['client.py'])


if __name__ == '__main__':
    unittest.main()
