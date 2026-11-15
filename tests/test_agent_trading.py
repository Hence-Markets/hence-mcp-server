"""Agent trading: a connected agent places real Hyperliquid perpetual orders itself, within the
limits the person set in Hence (get_trading_status, place_trade, `hence trade place|close|limits`,
and the card).

What these pin, in the order a mistake would cost money:
  * an order is reported as filled only when its own status is a fill. Every other status is
    said plainly, and an answer whose fields or figures do not agree with its status, a status
    this server does not know, or a missing field is refused whole as unreadable;
  * a lost answer is never a second order: client_order_id is required, sent as given, and the
    sentence for an unconfirmed order says to ask again with the same id;
  * the tools exist only behind their own switch (and sign-in), need their own permission
    (trades:place), and that permission is asked for at sign-in only while they are offered;
  * this server checks only the shape of an order and leaves the money to the app: no key, one
    path, and the words sent as given.
"""
import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from unittest import mock

from gateway import appauth, cards, config, protocol, shape, tools
from hence_cli import auth, main, render
from hence_cli.store import Store

from .support import StubUpstream, standard_routes
from .test_account import GRANT, ON, TOKEN, modern
from .test_cli import Hence, SERVER
from .test_research import App

STATUS = '/api/agents/v1/trading'
ORDERS = '/api/agents/v1/trading/orders'
PUBLIC = 'https://app.hence.markets'
SCOPES = ['account:read', 'portfolio:read', 'trades:place']
HERE = os.path.dirname(os.path.abspath(__file__))
TRADING_ON = dict(ON, HENCE_MCP_TRADING='1')
ASK = {'market_id': 'perp:BTC', 'side': 'long', 'size_usd': 200, 'leverage': 3, 'client_order_id': 'agent-7f3a.1'}


def order(**over):
    """An Order exactly as the app's contract has it: every field present, nullable ones as null."""
    o = {'order_id': 'ao_7Kq2mZx9Lp', 'market_id': 'perp:BTC', 'market': 'BTC', 'side': 'long', 'close': False, 'size_usd': 200.0, 'leverage': 3,
         'status': 'filled', 'filled_size': 0.00213, 'avg_price': 93512.5, 'filled_usd': 199.18, 'venue_order_id': 41234567890,
         'fee': 'Hence fee 0.04%, $0.08', 'problem': None, 'note': 'Asked for before the CPI print.', 'placed_at': '2026-11-16T09:30:12+00:00',
         'what_now': 'It filled. The position is in the person\'s Hence portfolio.'}
    o.update(over)
    return o


NO_FILL = {'filled_size': None, 'avg_price': None, 'filled_usd': None}
NOT_FILLED = order(status='not_filled', what_now='Nothing traded: the price moved past the slippage limit. The person holds what they held before.', **NO_FILL)
REJECTED = order(status='rejected', problem='Insufficient margin on the account.', venue_order_id=None,
                 what_now='The venue turned it down. The person holds what they held before.', **NO_FILL)
UNKNOWN = order(status='unknown', venue_order_id=None, what_now='The venue\'s answer was lost. Hence is checking it and takes no new orders until it knows.',
                **NO_FILL)
PARTLY = order(status='partly_filled', filled_size=0.001, avg_price=93500.0, filled_usd=93.5, what_now='Part of it went through; the rest was cancelled.')


def answer(o=None, **over):
    a = {'ok': True, 'placed': True, 'already_placed': False, 'problems': [], 'warnings': [], 'order': order() if o is None else o}
    a.update(over)
    return a


REFUSED = {'ok': False, 'placed': False, 'already_placed': False, 'warnings': [], 'order': None,
           'problems': [{'field': 'size_usd', 'problem': 'This is more than your limit of $500 per order.'}]}


def status(**over):
    s = {'status': 'active', 'wallet': '0x12…abcd', 'markets': 'Hyperliquid perpetuals',
         'limits': {'max_order_usd': 500, 'daily_usd': 2000, 'max_leverage': 5, 'max_slippage_pct': 1.0, 'expires_at': '2026-11-30T00:00:00+00:00'},
         'used_today_usd': 350.0, 'left_today_usd': 1650.0, 'problem': None,
         'how_to': 'The person changes the limits or turns agent trading off in Hence, under Settings.', 'recent': [order(), NOT_FILLED]}
    s.update(over)
    return s


# words that claim an order went through: never said by this server unless the order's status is a fill
CLAIMS = re.compile(r'(?i)placed|filled|executed|went through|bought|sold|opened a')


class Case(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, TRADING_ON)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.up = StubUpstream(standard_routes())

    def call(self, name, arguments=None, app=None, signed_in=True):
        headers = {'authorization': 'Bearer ' + TOKEN} if signed_in else None
        status_, out, extra = protocol.handle_full(*modern('tools/call', {'name': name, 'arguments': arguments or {}}, headers, name), self.up, auth=app)
        self.status, self.headers = status_, extra or {}
        return out if not isinstance(out, dict) or 'result' not in out else out['result']

    def value(self, *a, **k):
        result = self.call(*a, **k)
        self.assertEqual((self.status, result['isError']), (200, False), result)
        # the model reads the same answer as text
        self.assertEqual(json.loads(result['content'][0]['text']), result['structuredContent'])
        return result['structuredContent']

    def refusal(self, *a, **k):
        result = self.call(*a, **k)
        self.assertEqual(self.status, 200)
        self.assertIs(result['isError'], True, result)
        self.assertNotIn('structuredContent', result)
        return result['content'][0]['text']


class OnlyBehindItsSwitchAndItsPermission(Case):
    def names(self):
        _, out = protocol.handle(*modern('tools/list'), self.up)
        return [t['name'] for t in out['result']['tools']]

    def test_the_tools_are_absent_unless_both_switches_are_on(self):
        self.assertTrue(config.trading_active())
        self.assertEqual(self.names()[-2:], ['get_trading_status', 'place_trade'])
        self.assertIn('trades:place', protocol.resource_metadata()['scopes_supported'])
        app = App({ORDERS: answer(), STATUS: status()}, scopes=SCOPES)
        for off in ({'HENCE_MCP_TRADING': '0'}, {'HENCE_MCP_TRADING': 'true'}, {'HENCE_MCP_TRADING': ''}, {'HENCE_MCP_ACCOUNTS': '0'},
                    {'HENCE_MCP_GATEWAY_SECRET': ''}):
            with mock.patch.dict(os.environ, off):
                self.assertFalse(config.trading_active(), off)
                self.assertNotIn('place_trade', self.names(), off)
                self.assertNotIn('get_trading_status', self.names(), off)
                self.assertIsNone(tools.find('place_trade'))
                # so sign-in never asks for it
                self.assertNotIn('trades:place', protocol.resource_metadata()['scopes_supported'], off)
                for name, args in (('place_trade', ASK), ('get_trading_status', {})):
                    self.assertIn('Unknown tool', self.call(name, args, app)['error']['message'])
        self.assertEqual((app.sent, app.read), ([], []))
        # its own switch: preparing trades does not turn it on, and it does not need preparing turned on
        with mock.patch.dict(os.environ, {'HENCE_MCP_TRADES': '1', 'HENCE_MCP_TRADING': '0'}):
            self.assertIn('propose_trade', self.names())
            self.assertNotIn('place_trade', self.names())
        self.assertNotIn('propose_trade', self.names())

    def test_it_needs_trades_place_and_a_permission_to_prepare_is_not_one_to_place(self):
        app = App({ORDERS: answer(), STATUS: status()}, scopes=['account:read', 'portfolio:read', 'trades:propose'])
        for name, args in (('place_trade', ASK), ('get_trading_status', {})):
            self.call(name, args, app)
            self.assertEqual(self.status, 403, name)
            self.assertIn('scope="trades:place"', self.headers['WWW-Authenticate'])
            self.call(name, args, app, signed_in=False)
            self.assertEqual(self.status, 401, name)
            self.assertIn('scope="trades:place"', self.headers['WWW-Authenticate'])
        self.assertEqual((app.sent, app.read), ([], []))
        self.assertEqual([t['scope'] for t in tools.TRADING_TOOLS], ['trades:place', 'trades:place'])
        for t in tools.TRADING_TOOLS:
            self.assertNotIn('scope', tools.public_definition(t))

    def test_the_command_asks_for_trades_place_when_it_signs_in(self):
        # the command asks for exactly what the server lists in its sign-in metadata
        class Server(Hence):
            def __call__(hence, url, method, headers, body, timeout):
                if '/.well-known/oauth-protected-resource' in url:
                    hence.requests.append((method, url, dict(headers), body))
                    return hence._json(200, protocol.resource_metadata())
                return Hence.__call__(hence, url, method, headers, body, timeout)
        hence = Server()
        with tempfile.TemporaryDirectory() as d:
            out, err = io.StringIO(), io.StringIO()
            code = main.run(['--server', SERVER, 'login'], out=out, err=err, store=Store(os.path.join(d, 'c.json')), sender=hence,
                            open_browser=hence.browser())
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn('trades:place', hence.authorize['scope'].split())
        self.assertEqual(auth.discover(SERVER, hence)['scopes'], protocol.resource_metadata()['scopes_supported'])

    def test_one_tool_reads_and_one_places_and_says_it_cannot_be_undone(self):
        get, place = tools.TRADING_TOOLS
        self.assertEqual((get['name'], place['name']), ('get_trading_status', 'place_trade'))
        self.assertEqual((get['annotations']['readOnlyHint'], get['annotations']['destructiveHint']), (True, False))
        self.assertEqual((place['annotations']['readOnlyHint'], place['annotations']['destructiveHint']), (False, True))
        # a retry with the same client_order_id reaches the same order: the call is idempotent, and only because the id is required
        self.assertIs(place['annotations']['idempotentHint'], True)
        self.assertIn('client_order_id', tools.PLACE_TRADE['required'])

    def test_the_description_says_what_it_does_and_when_not_to(self):
        said = tools.TRADING_TOOLS[1]['description']
        for part in ('Places a REAL order with the person\'s money, immediately', 'Use it only when the person asked for this trade',
                     'standing instruction', 'agent trading is active', 'call get_trading_status first if unsure', 'propose_trade',
                     'Always pass a client_order_id', 'reuse it', 'never a second one', 'not the margin', 'filled_size and avg_price',
                     'say plainly that it did not fill', 'Pass on every warning'):
            self.assertIn(part, said, part)
        self.assertEqual(tools.TRADING_TOOLS[1]['unread'],
                         'Hence did not confirm the order. Ask again with the same client_order_id: it reaches the same order, never a second one.')
        reading = tools.TRADING_TOOLS[0]['description']
        for part in ('What you may do on your own right now, and what you already did', 'how_to', 'propose_trade', 'not zero'):
            self.assertIn(part, reading, part)

    def test_propose_trade_names_place_trade_only_while_it_is_offered(self):
        with mock.patch.dict(os.environ, {'HENCE_MCP_TRADES': '1'}):
            propose = tools.find('propose_trade')
            self.assertTrue(propose['description'].endswith(tools.PLACE_DIRECTLY))
            self.assertIn('place_trade places it directly', propose['description'])
            with mock.patch.dict(os.environ, {'HENCE_MCP_TRADING': '0'}):
                self.assertNotIn('place_trade', tools.find('propose_trade')['description'])
        self.assertNotIn('place_trade', tools.TRADE_TOOLS[0]['description'])        # the list itself is never changed

    def test_what_the_server_says_about_trading_is_true_either_way(self):
        _, out = protocol.handle(*modern('server/discover'), self.up)
        said = out['result']['instructions']
        self.assertNotIn('places a trade or moves money', said)
        self.assertIn(protocol.WITH_TRADING, said)
        found = tools.search_markets({'query': 'btc', 'kind': 'perp'}, self.up)
        self.assertNotIn('cannot place trades', found['how_to_trade'])
        self.assertIn('place_trade', found['how_to_trade'])
        with mock.patch.dict(os.environ, {'HENCE_MCP_TRADING': '0'}):
            _, out = protocol.handle(*modern('server/discover'), self.up)
            self.assertIn(protocol.NO_TRADING, out['result']['instructions'])
            self.assertIn('cannot place trades', tools.search_markets({'query': 'btc', 'kind': 'perp'}, self.up)['how_to_trade'])

    def test_one_path_and_no_key(self):
        self.assertEqual(appauth.ORDER_PATH, ORDERS)
        self.assertIn(ORDERS, appauth.WRITE_PATHS)
        self.assertTrue(appauth.allowed(STATUS))
        for bad in (ORDERS, ORDERS + '/ao_1', STATUS + '/limits', STATUS + '/key', '/api/agents/v1/trading/withdraw'):
            self.assertFalse(appauth.allowed(bad), bad)
        self.assertEqual([p for p in appauth.WRITE_PATHS if 'trading' in p], [ORDERS])
        # no argument can name whose money, which wallet, a key, a withdrawal, or what the app's limits decide
        self.assertEqual(sorted(tools.PLACE_TRADE['properties']), ['client_order_id', 'close', 'leverage', 'market_id', 'note', 'side', 'size_usd'])
        for arg in tools.PLACE_TRADE['properties']:
            self.assertNotRegex(arg, r'(?i)wallet|address|user|account|key|signature|nonce|slippage|limit|fee|withdraw|builder|vault')
        self.assertIs(tools.PLACE_TRADE['additionalProperties'], False)
        self.assertNotIn('anyOf', json.dumps(tools.PLACE_TRADE))           # hosts refuse a top-level combinator; the rule is the handler's
        self.assertEqual(tools.TRADING_STATUS, {'type': 'object', 'additionalProperties': False, 'properties': {}})


class Placing(Case):
    def test_an_order_is_sent_as_given_and_its_fill_is_passed_on(self):
        app = App({ORDERS: answer(warnings=['3x leverage: a 33% move against it closes the position.'])}, scopes=SCOPES)
        got = self.value('place_trade', dict(ASK, note='Asked for before the CPI print.'), app)
        self.assertEqual(app.sent, [(ORDERS, dict(ASK, note='Asked for before the CPI print.'))])
        o = got['order']
        self.assertEqual((o['status'], o['filled_size'], o['avg_price'], o['filled_usd'], o['fee']), ('filled', 0.00213, 93512.5, 199.18, 'Hence fee 0.04%, $0.08'))
        self.assertEqual(sorted(o), sorted(list(shape._ORDER) + ['market_url']))         # the contract's fields, and where the market is in Hence
        self.assertEqual(o['market_url'], 'https://app.hence.markets/m/hl/BTC')
        self.assertEqual((got['ok'], got['placed'], got['already_placed'], got['client_order_id']), (True, True, False, 'agent-7f3a.1'))
        self.assertEqual(got['warnings'], ['3x leverage: a 33% move against it closes the position.'])
        for said in ('The order filled', 'filled_size at avg_price', 'the fee', 'Pass on every warning'):
            self.assertIn(said, got['next'], said)

    def test_a_close_sends_close_and_only_what_was_given(self):
        app = App({ORDERS: answer(order(close=True, size_usd=None, leverage=None))}, scopes=SCOPES)
        got = self.value('place_trade', {'market_id': 'perp:xyz:NVDA', 'close': True, 'client_order_id': 'c1'}, app)
        self.assertEqual(app.sent, [(ORDERS, {'market_id': 'perp:xyz:NVDA', 'close': True, 'client_order_id': 'c1'})])
        self.assertEqual((got['order']['close'], got['order']['size_usd']), (True, None))
        self.value('place_trade', {'market_id': 'perp:xyz:NVDA', 'close': True, 'size_usd': 50.5, 'side': 'short', 'client_order_id': 'c2'}, app)
        self.assertEqual(app.sent[-1], (ORDERS, {'market_id': 'perp:xyz:NVDA', 'close': True, 'size_usd': 50.5, 'side': 'short', 'client_order_id': 'c2'}))
        # close: false is an opening order, sent without the flag
        self.value('place_trade', dict(ASK, close=False, note='  '), app)
        self.assertEqual(app.sent[-1], (ORDERS, ASK))

    def test_the_shape_is_checked_here_and_nothing_is_sent_when_it_is_wrong(self):
        app = App({ORDERS: answer()}, scopes=SCOPES)
        bad = (({'side': 'long', 'size_usd': 200, 'client_order_id': 'x'}, '`market_id`'),
               (dict(ASK, market_id='spot:4663:0x' + 'a' * 40), 'Only perpetuals'), (dict(ASK, market_id='hip4:1212:0'), 'Only perpetuals'),
               (dict(ASK, market_id='BTC'), 'Only perpetuals'), (dict(ASK, market_id='perp:BTC/../x'), 'Only perpetuals'),
               ({k: v for k, v in ASK.items() if k != 'client_order_id'}, '`client_order_id` is required'),
               (dict(ASK, client_order_id=''), '`client_order_id` is required'), (dict(ASK, client_order_id='has space'), '`client_order_id` is required'),
               (dict(ASK, client_order_id='x' * 65), '`client_order_id` is required'), (dict(ASK, client_order_id=7), '`client_order_id` is required'),
               ({k: v for k, v in ASK.items() if k != 'side'}, '`side` is required'), (dict(ASK, side='buy'), '`side` must be one of'),
               ({k: v for k, v in ASK.items() if k != 'size_usd'}, '`size_usd` is required unless `close`'),
               (dict(ASK, size_usd=0), 'above 0'), (dict(ASK, size_usd=-5), 'above 0'), (dict(ASK, size_usd='200'), 'above 0'),
               (dict(ASK, size_usd=True), 'above 0'), (dict(ASK, size_usd=float('nan')), 'above 0'), (dict(ASK, size_usd=float('inf')), 'above 0'),
               (dict(ASK, leverage=0), '`leverage`'), (dict(ASK, leverage=51), '`leverage`'), (dict(ASK, leverage=2.5), '`leverage`'),
               ({'market_id': 'perp:BTC', 'close': True, 'leverage': 3, 'client_order_id': 'x'}, 'Leave it out with `close`'),
               (dict(ASK, close='yes'), '`close`'), (dict(ASK, note='x' * 281), '`note`'),
               (dict(ASK, wallet='0x1'), 'Unknown argument'), (dict(ASK, slippage_pct=5), 'Unknown argument'), (dict(ASK, reduce_only=True), 'Unknown argument'))
        for args, said in bad:
            self.assertIn(said, self.refusal('place_trade', args, app), repr(args)[:90])
        self.assertEqual(app.sent, [])

    def test_text_for_an_order_that_did_not_fill_never_claims_it_did(self):
        for o, words in ((NOT_FILLED, 'It did not fill'), (REJECTED, 'rejected and did not fill'), (UNKNOWN, 'Hence does not know yet')):
            got = self.value('place_trade', ASK, App({ORDERS: answer(o)}, scopes=SCOPES))
            self.assertEqual(got['order']['status'], o['status'])
            self.assertIn(words, got['next'])
            # neither this server's sentence nor the app's what_now for these says the order went through
            self.assertNotRegex(got['next'], CLAIMS, o['status'])
            self.assertNotRegex(got['order']['what_now'], CLAIMS, o['status'])
            self.assertEqual((got['order']['filled_size'], got['order']['avg_price']), (None, None))
        unknown = self.value('place_trade', ASK, App({ORDERS: answer(UNKNOWN)}, scopes=SCOPES))['next']
        for said in ('Do not tell the person it did or did not fill', 'say that Hence is checking', 'call get_trading_status'):
            self.assertIn(said, unknown)
        partly = self.value('place_trade', ASK, App({ORDERS: answer(PARTLY)}, scopes=SCOPES))['next']
        self.assertIn('Only part of the order filled', partly)
        self.assertIn('The rest of the size asked for did not fill', partly)

    def test_the_same_id_again_is_the_same_order(self):
        got = self.value('place_trade', ASK, App({ORDERS: answer(already_placed=True)}, scopes=SCOPES))
        self.assertIs(got['already_placed'], True)
        self.assertTrue(got['next'].startswith('This client_order_id was used before: this is that same order, not a new one.'))
        # an app that says "already placed" or "placed" must carry the order, whatever `placed` says of this request
        self.assertIsNotNone(shape.order_placed(answer(placed=False, already_placed=True), PUBLIC))

    def test_problems_mean_no_order_and_say_so(self):
        got = self.value('place_trade', dict(ASK, size_usd=900), App({ORDERS: REFUSED}, scopes=SCOPES))
        self.assertEqual((got['ok'], got['placed'], got['order'], got['problems'][0]['field']), (False, False, None, 'size_usd'))
        self.assertTrue(got['next'].startswith('No order was placed.'))
        self.assertIn('propose_trade', got['next'])
        # the venue said no: a refusal that carries the order, which did not fill
        venue = self.value('place_trade', ASK, App({ORDERS: dict(REFUSED, placed=True, order=REJECTED)}, scopes=SCOPES))
        self.assertEqual(venue['order']['status'], 'rejected')
        self.assertIn('Tell the person each of `problems` too', venue['next'])
        self.assertNotRegex(venue['next'], CLAIMS)

    def test_a_refusal_of_the_whole_order_says_no_order_was_placed_and_what_next(self):
        for code, said, next_ in (('not_set_up', 'Agent trading is not set up for this agent.', 'propose_trade'),
                                  ('paused', 'Agent trading is paused: the daily limit was reached.', 'propose_trade'),
                                  ('expired', 'Agent trading for this agent ended on 2026-11-15.', 'turn agent trading on again'),
                                  ('earlier_order_unknown', 'Hence is still checking an earlier order.', 'get_trading_status')):
            app = App({ORDERS: appauth.Refused(409, code, said)}, scopes=SCOPES)
            text = self.refusal('place_trade', ASK, app)
            self.assertTrue(text.startswith(said + ' No '), text)
            self.assertIn('order was placed', text)
            self.assertIn(next_, text)
            self.assertNotRegex(text.replace('order was placed', ''), CLAIMS)
        # a refusal the contract does not name is passed on in Hence's own words, with nothing added
        other = 'This market is closed for agent orders.'
        self.assertEqual(self.refusal('place_trade', ASK, App({ORDERS: appauth.Refused(409, 'market_closed', other)}, scopes=SCOPES)), other)

    def test_no_answer_is_never_an_order_and_says_to_ask_again_with_the_same_id(self):
        for failure in (appauth.Unavailable('Hence answered 503'), appauth.Unavailable('Hence could not be reached (TimeoutError)'),
                        answer(order(status='cancelled')), answer(order(filled_size=None)), {'ok': True}):
            text = self.refusal('place_trade', ASK, App({ORDERS: failure}, scopes=SCOPES))
            self.assertIn('Hence did not confirm the order. Ask again with the same client_order_id: it reaches the same order, never a second one.', text)
            self.assertNotRegex(text, r'(?i)\bplaced\b|\bfilled\b|not placed|did not fill')
        self.assertIn('could not be read (order)', self.refusal('place_trade', ASK, App({ORDERS: answer(order(status='cancelled'))}, scopes=SCOPES)))

    def test_the_real_signed_call_reaches_the_order_path_with_the_body_signed(self):
        sent = []

        def send(url, method, headers, body, timeout):
            sent.append((url, method, headers, body))
            return 503, json.dumps({'error': 'temporarily_unavailable', 'error_description': 'Try again shortly.'}).encode(), {}
        with self.assertRaises(appauth.Unavailable):
            appauth.AppAuth(base='https://app.example', send=send).post(GRANT, ORDERS, ASK)
        url, method, headers, body = sent[0]
        self.assertEqual((url, method, json.loads(body)), ('https://app.example' + ORDERS, 'POST', ASK))
        self.assertEqual(headers['X-Hence-Agent-Grant'], GRANT)
        self.assertNotIn('Authorization', headers)                       # the person's token is never forwarded


class StrictOrders(unittest.TestCase):
    """shape.agent_order and shape.order_placed: an order is passed on whole and as the contract
    says it is, or not at all."""

    def test_every_status_of_the_contract_is_read(self):
        for o in (order(), PARTLY, NOT_FILLED, REJECTED, UNKNOWN, order(close=True, size_usd=None), order(market_id='perp:xyz:NVDA', market='NVDA')):
            self.assertIsNotNone(shape.agent_order(o, PUBLIC), o['status'])
        self.assertEqual(shape.agent_order(order(market_id='perp:xyz:NVDA'), PUBLIC)['market_url'], 'https://app.hence.markets/m/xyz/xyz%3ANVDA')
        # an empty text field is none, not unreadable; a zero fill on an order that did not fill is a fact
        self.assertIsNone(shape.agent_order(order(fee='', note='  '), PUBLIC)['fee'])
        self.assertEqual(shape.agent_order(dict(NOT_FILLED, filled_size=0, filled_usd=0), PUBLIC)['filled_size'], 0)

    def test_a_status_this_server_does_not_know_refuses_the_whole_answer(self):
        for bad in ('cancelled', 'open', 'resting', 'pending', 'FILLED', 'filled ', None, True, 1):
            self.assertIsNone(shape.agent_order(order(status=bad), PUBLIC), repr(bad))
            self.assertIsNone(shape.order_placed(answer(order(status=bad)), PUBLIC), repr(bad))
            self.assertIsNone(shape.trading_status(status(recent=[order(), order(status=bad)]), PUBLIC), repr(bad))

    def test_a_missing_field_refuses_the_whole_answer(self):
        for key in shape._ORDER:
            broken = order()
            del broken[key]
            self.assertIsNone(shape.agent_order(broken, PUBLIC), key)
            self.assertIsNone(shape.order_placed(answer(broken), PUBLIC), key)

    def test_a_field_of_the_wrong_kind_refuses_the_whole_answer(self):
        for over in ({'order_id': 'pr_' + 'a' * 22}, {'order_id': 'ao_'}, {'order_id': 7}, {'market_id': 'spot:4663:0x' + 'a' * 40}, {'market_id': 'BTC'},
                     {'market': ''}, {'market': None}, {'side': 'buy'}, {'side': None}, {'close': 'false'}, {'close': None}, {'size_usd': 0}, {'size_usd': '200'},
                     {'size_usd': None}, {'leverage': 0}, {'leverage': 2.5}, {'leverage': True}, {'filled_size': '0.002'}, {'avg_price': float('nan')},
                     {'venue_order_id': '4123'}, {'venue_order_id': -1}, {'fee': 0.08}, {'problem': ['x']}, {'note': 7}, {'placed_at': None},
                     {'what_now': ''}, {'what_now': None}):
            self.assertIsNone(shape.agent_order(order(**over), PUBLIC), over)
        for bad in (None, 'x', [], 7):
            self.assertIsNone(shape.agent_order(bad, PUBLIC))

    def test_figures_must_agree_with_the_status(self):
        contradictions = (order(filled_size=None), order(avg_price=None), order(filled_size=0), order(avg_price=0), order(filled_usd=-1),
                          dict(PARTLY, filled_size=None),
                          dict(NOT_FILLED, filled_size=0.001), dict(NOT_FILLED, avg_price=93000.0), dict(NOT_FILLED, filled_usd=5.0),
                          dict(UNKNOWN, filled_size=0.002, avg_price=93500.0), dict(REJECTED, avg_price=1.0))
        for o in contradictions:
            self.assertIsNone(shape.agent_order(o, PUBLIC), o)

    def test_the_answer_and_its_order_must_agree(self):
        broken = ({}, {'ok': True}, answer(ok='yes'), answer(placed=None), answer(already_placed=None), answer(problems=None), answer(warnings=None),
                  answer(order=None),                                                  # "placed" with no order
                  dict(REFUSED, already_placed=True),                                  # "already placed" with no order
                  answer(problems=[{'field': 'x', 'problem': 'y'}]),                   # a fill and a problem
                  dict(REFUSED, problems=[]),                                          # a refusal with no reason
                  dict(REFUSED, order=order()),                                        # a refusal beside a fill
                  dict(REFUSED, order=PARTLY), answer(order(status='cancelled')), 'text', 7, None)
        for a in broken:
            self.assertIsNone(shape.order_placed(a, PUBLIC), repr(a)[:100])


class Status(Case):
    def test_the_status_is_passed_on_with_its_limits_and_recent_orders(self):
        app = App({STATUS: status()}, scopes=SCOPES)
        got = self.value('get_trading_status', {}, app)
        self.assertEqual(app.read, [STATUS])
        self.assertEqual((got['status'], got['wallet'], got['markets'], got['used_today_usd'], got['left_today_usd']),
                         ('active', '0x12…abcd', 'Hyperliquid perpetuals', 350.0, 1650.0))
        self.assertEqual(got['limits'], {'max_order_usd': 500, 'daily_usd': 2000, 'max_leverage': 5, 'max_slippage_pct': 1.0, 'expires_at': '2026-11-30T00:00:00+00:00'})
        self.assertEqual([o['status'] for o in got['recent']], ['filled', 'not_filled'])
        self.assertIn('place_trade places Hyperliquid perpetual orders', got['next'])
        self.assertIn('only "filled" and "partly_filled" are fills', got['next'])
        self.assertIn('not zero', got['next'])
        self.assertEqual(self.value('get_trading_status', {}, App({STATUS: status(used_today_usd=None, left_today_usd=None, recent=[])}, scopes=SCOPES))['left_today_usd'],
                         None)
        self.assertIn('Unknown argument', self.refusal('get_trading_status', {'wallet': '0x1'}, app))

    def test_not_active_says_place_trade_will_be_refused_and_what_to_do(self):
        for state in ('not_set_up', 'waiting_for_wallet', 'paused', 'expired'):
            s = status(status=state, limits=None if state == 'not_set_up' else status()['limits'], wallet=None if state == 'not_set_up' else '0x12…abcd',
                       problem='The daily limit was reached.' if state == 'paused' else None, recent=[])
            got = self.value('get_trading_status', {}, App({STATUS: s}, scopes=SCOPES))
            self.assertEqual(got['status'], state)
            for said in ('Agent trading is not active (%s)' % state, 'place_trade will be refused', 'how_to', 'propose_trade'):
                self.assertIn(said, got['next'], state)
            self.assertEqual('problem says' in got['next'], state == 'paused')

    def test_an_answer_that_cannot_be_read_is_refused_whole(self):
        broken = [status(status='on'), status(status=None), status(limits=None), status(limits={'max_order_usd': 500}),
                  status(limits=dict(status()['limits'], daily_usd=None)), status(limits=dict(status()['limits'], max_leverage=2.5)),
                  status(limits=dict(status()['limits'], max_order_usd=-1)), status(limits=dict(status()['limits'], expires_at='')),
                  status(wallet='0x' + 'a' * 40), status(wallet=7), status(markets=''), status(how_to=None), status(recent=None),
                  status(recent=[order(), order(filled_size=None)]), status(used_today_usd='350'), status(problem=7), {}, 'x', None]
        for key in ('status', 'wallet', 'markets', 'limits', 'used_today_usd', 'left_today_usd', 'problem', 'how_to', 'recent'):
            s = status()
            del s[key]
            broken.append(s)
        for s in broken:
            self.assertIsNone(shape.trading_status(s, PUBLIC), repr(s)[:100])
        text = self.refusal('get_trading_status', {}, App({STATUS: status(limits=None)}, scopes=SCOPES))
        self.assertIn('could not be read (agent trading)', text)
        self.assertIn('Agent trading was not read.', text)


class TheCard(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, '..', 'data', 'thesis-card.html'), encoding='utf-8') as f:
            self.html = f.read()

    def test_both_tools_point_at_the_card(self):
        self.assertIn('place_trade', cards.TOOLS)
        self.assertIn('get_trading_status', cards.TOOLS)
        for t in tools.TRADING_TOOLS:
            self.assertEqual(tools.public_definition(t)['_meta'], {'ui': {'resourceUri': 'ui://hence/thesis-card'}})

    def test_a_placed_order_is_drawn_and_a_fill_only_from_a_fill(self):
        html = self.html
        for said in ("head(card, 'Placed by your agent', stood[0], stood[1]);",
                     "filled: ['Filled', 'pos'], partly_filled: ['Partly filled', 'lav'], not_filled: ['Not filled', 'neg'], rejected: ['Rejected', 'neg'], unknown: ['Unknown', 'warn']",
                     "function isFill(o) { return o.status === 'filled' || o.status === 'partly_filled'; }",
                     "(isFill(o) ? [['Filled at', px(o.avg_price)], ['Filled size', units(o.filled_size, o), usd(o.filled_usd)], ['Leverage', lev]]",
                     ": [['Filled', o.status === 'unknown' ? 'Not known yet' : 'Nothing']",
                     "title.appendChild(logo(o.market_id, o.market_id, 'lg'));",
                     "if (o.problem) card.appendChild(el('div', 'warnline', o.problem));",
                     "stand.appendChild(el('span', '', o.what_now || 'Hence did not say what happens next.'));",
                     "card.appendChild(el('div', 'label', 'The agent\\'s reason, in its own words')); card.appendChild(el('p', 'note', o.note));",
                     "else if (value.order && typeof value.order === 'object' && typeof value.order.order_id === 'string') show(placedOrder(value.order, value));",
                     "show(refused(value, 'No order was placed', 'Not placed'));"):
            self.assertTrue(said in html, said)
        # every field the card reads is one the shaper passes on
        got = shape.order_placed(answer(), PUBLIC)
        for field in ('status', 'market', 'market_id', 'side', 'close', 'size_usd', 'leverage', 'filled_size', 'avg_price', 'filled_usd', 'fee', 'problem',
                      'note', 'what_now', 'placed_at', 'order_id'):
            self.assertIn(field, got['order'], field)
            self.assertTrue('o.' + field in html, 'o.' + field)
        for field in ('problems', 'warnings', 'already_placed'):
            self.assertTrue('answer.' + field in html, field)
        # it has no button and no link of its own: the app gives an order none, and nothing here changes an order
        self.assertNotIn("openButton(o.", html)
        self.assertIn("[value.order], value.recent", html)                 # its market's address in Hence is where the logo comes from
        self.assertIn("found = origin(x.url || x.market_url)", html)

    def test_the_status_is_drawn_from_what_was_read(self):
        html = self.html
        for said in ("head(card, 'Agent trading', w[0], w[1]);",
                     "strip(card, [['Per order', usd(l.max_order_usd)], ['Per day', usd(l.daily_usd)], ['Leverage up to', isNum(l.max_leverage) ? l.max_leverage + '×' : 'n/a'],",
                     "strip(card, [['Used today', usd(value.used_today_usd)], ['Left today', usd(value.left_today_usd)], ['Daily limit', usd(l.daily_usd)]]);",
                     "if (isNum(value.used_today_usd) && isNum(l.daily_usd) && l.daily_usd > 0) {",         # no use read: no bar
                     "if (value.status === 'active' && l.expires_at) clock(card.appendChild(el('div', 'small', '')), l.expires_at,",
                     "if (value.problem) card.appendChild(el('div', 'warnline', value.problem));",
                     "if (!rows.length) card.appendChild(el('div', 'state', 'No orders yet.'));",
                     "else if (typeof value.status === 'string' && 'limits' in value && Array.isArray(value.recent)) show(trading(value));"):
            self.assertTrue(said in html, said)
        got = shape.trading_status(status(), PUBLIC)
        for field in ('status', 'wallet', 'markets', 'limits', 'used_today_usd', 'left_today_usd', 'problem', 'how_to', 'recent'):
            self.assertIn(field, got, field)
            self.assertTrue('value.' + field in html, 'value.' + field)
        for field in ('max_order_usd', 'daily_usd', 'max_leverage', 'max_slippage_pct', 'expires_at'):
            self.assertTrue('l.' + field in html, 'l.' + field)

    def test_the_new_views_keep_every_promise_of_the_page(self):
        html = self.html
        # still one place an image is made, two buttons that change only what the page shows, one way to ask the host for anything
        self.assertEqual(html.count("document.createElement('img')"), 1)
        self.assertEqual(len(re.findall(r"el\('button'", html)), 2)
        self.assertEqual(len(re.findall(r"request\('ui/open-link'", html)), 1)
        self.assertNotRegex(html, r'innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function')
        self.assertNotRegex(html, r'\bfetch\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon')
        self.assertNotRegex(html, r"\|\|\s*0\b")
        self.assertNotRegex(html, r"el\('(input|select|textarea)'|<input|<form")
        self.assertIn('.meter i { display: block; height: 100%; border-radius: inherit; background: var(--lav); }', html)


class Command(unittest.TestCase):
    """`hence trade place|close|limits`, with the server's own tool functions behind them."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = Store(os.path.join(self.dir.name, 'credentials.json'))
        self.store.update(SERVER, access_token='hat_x', expires_at=None)
        self.app = None
        patcher = mock.patch.dict(os.environ, TRADING_ON)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sender(self, url, method, headers, body, timeout):
        msg = json.loads(body)
        name, args = msg['params']['name'], msg['params']['arguments']

        class Account:
            def get(account, path, query=None):
                return self.app.get(GRANT, path, query)

            def post(account, path, payload):
                return self.app.post(GRANT, path, payload)
        try:
            value = {'place_trade': tools.place_trade, 'get_trading_status': tools.get_trading_status}[name](args, None, Account())
            result = {'content': [{'type': 'text', 'text': json.dumps(value)}], 'structuredContent': value, 'isError': False}
        except (appauth.Unavailable, appauth.Refused, tools.ToolError) as e:
            result = {'content': [{'type': 'text', 'text': str(e)}], 'isError': True}
        return 200, json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': result}).encode(), {}

    def cli(self, *argv, confirm=None):
        out, err = io.StringIO(), io.StringIO()
        code = main.run(list(argv), out=out, err=err, store=self.store, sender=self.sender, confirm=confirm)
        return code, out.getvalue(), err.getvalue()

    def test_place_asks_first_and_no_sends_nothing(self):
        self.app = App({ORDERS: answer()}, scopes=SCOPES)
        code, out, err = self.cli('trade', 'place', 'perp:BTC', 'long', '200', '--leverage', '3', '--id', 'mine-1', confirm=lambda: False)
        self.assertEqual((code, out, self.app.sent), (1, '', []))
        self.assertIn('Place now, for real, with your money: long perp:BTC, $200.00 at 3x', err)
        self.assertIn('Not placed. To place without being asked, add --yes.', err)

    def test_place_sends_what_was_typed_with_the_id_and_prints_the_fill(self):
        self.app = App({ORDERS: answer()}, scopes=SCOPES)
        code, out, err = self.cli('trade', 'place', 'perp:BTC', 'long', '200', '--leverage', '3', '--note', 'Before the CPI print.', '--id', 'mine-1',
                                  confirm=lambda: True)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.app.sent, [(ORDERS, {'market_id': 'perp:BTC', 'client_order_id': 'mine-1', 'side': 'long', 'size_usd': 200, 'leverage': 3,
                                                   'note': 'Before the CPI print.'})])
        self.assertIn('Order id mine-1. If this does not finish, run the same command with --id mine-1', err)
        for said in ('Long BTC  $200.00 at 3x  [filled]', 'filled 0.00213 BTC at 93512.50, worth $199.18', 'fee: Hence fee 0.04%, $0.08',
                     'client order id: mine-1', 'market in Hence: https://app.hence.markets/m/hl/BTC'):
            self.assertIn(said, out, said)

    def test_without_an_id_one_is_made_printed_first_and_sent(self):
        self.app = App({ORDERS: answer()}, scopes=SCOPES)
        code, out, err = self.cli('trade', 'place', 'perp:xyz:NVDA', 'short', '1250.5', '--yes')
        self.assertEqual(code, 0, err)
        made = re.search(r'Order id (cli-[A-Za-z0-9_-]{16})\. If this does not finish, run the same command with --id \1:', err)
        self.assertIsNotNone(made, err)
        self.assertEqual(self.app.sent, [(ORDERS, {'market_id': 'perp:xyz:NVDA', 'client_order_id': made.group(1), 'side': 'short', 'size_usd': 1250.5})])
        self.assertTrue(shape.CLIENT_ORDER_ID_RE.match(made.group(1)))
        # a new one each time: two orders are never one by accident
        self.cli('trade', 'place', 'perp:xyz:NVDA', 'short', '1250.5', '--yes')
        self.assertNotEqual(self.app.sent[0][1]['client_order_id'], self.app.sent[1][1]['client_order_id'])

    def test_close_sends_close_and_the_size_only_when_typed(self):
        self.app = App({ORDERS: answer(order(close=True, size_usd=None, leverage=None))}, scopes=SCOPES)
        code, out, err = self.cli('trade', 'close', 'perp:BTC', '--id', 'c-1', confirm=lambda: True)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.app.sent, [(ORDERS, {'market_id': 'perp:BTC', 'client_order_id': 'c-1', 'close': True})])
        self.assertIn('Close BTC  all of it  [filled]', out)
        code, out, err = self.cli('trade', 'close', 'perp:BTC', '100', '--id', 'c-2', confirm=lambda: False)
        self.assertEqual((code, len(self.app.sent)), (1, 1))
        self.assertIn('close $100.00 of your perp:BTC position', err)
        self.cli('trade', 'close', 'perp:BTC', '100', '--id', 'c-2', '--yes')
        self.assertEqual(self.app.sent[-1], (ORDERS, {'market_id': 'perp:BTC', 'client_order_id': 'c-2', 'close': True, 'size_usd': 100}))

    def test_an_order_that_did_not_fill_says_so_and_exits_1(self):
        for o, said in ((NOT_FILLED, 'Did not fill.'), (REJECTED, 'Did not fill.'), (UNKNOWN, 'Not known yet whether it went through')):
            self.app = App({ORDERS: answer(o)}, scopes=SCOPES)
            code, out, err = self.cli('trade', 'place', 'perp:BTC', 'long', '200', '--yes')
            self.assertEqual(code, 1, o['status'])
            self.assertIn(said, out)
            self.assertIn('[%s]' % o['status'], out)
            self.assertNotIn('filled 0', out)
            self.assertNotRegex(out.replace('[not_filled]', ''), r'(?i)\bfilled\b')
        self.app = App({ORDERS: REFUSED}, scopes=SCOPES)
        code, out, err = self.cli('trade', 'place', 'perp:BTC', 'long', '900', '--yes')
        self.assertEqual(code, 1)
        self.assertIn('1 problem. No order was placed.', out)
        self.assertIn('size_usd: This is more than your limit of $500 per order.', out)

    def test_a_refusal_and_a_bad_id_are_stderr_and_exit_1(self):
        self.app = App({ORDERS: appauth.Refused(409, 'paused', 'Agent trading is paused.')}, scopes=SCOPES)
        code, out, err = self.cli('trade', 'place', 'perp:BTC', 'long', '200', '--yes')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('Agent trading is paused. No order was placed.', err)
        for bad in ('has space', 'x' * 65, 'é'):
            code, out, err = self.cli('trade', 'place', 'perp:BTC', 'long', '200', '--id', bad, '--yes')
            self.assertEqual((code, out), (1, ''), bad)
            self.assertIn('--id is 1 to 64 letters', err)
        for size in ('nan', 'inf'):
            code, out, err = self.cli('trade', 'place', 'perp:BTC', 'long', size, '--yes')
            self.assertEqual((code, out), (1, ''), size)
            self.assertIn('must be a number of US dollars', err)
        self.assertEqual(self.app.sent, [(ORDERS, {'market_id': 'perp:BTC', 'client_order_id': self.app.sent[0][1]['client_order_id'], 'side': 'long',
                                                   'size_usd': 200})])

    def test_parsing(self):
        p = main._parser()
        ns = p.parse_args(['trade', 'place', 'perp:BTC', 'short', '200', '--leverage', '2', '--note', 'n', '--id', 'abc', '--yes'])
        self.assertEqual((ns.what, ns.market, ns.side, ns.size, ns.leverage, ns.note, ns.client_id, ns.yes),
                         ('place', 'perp:BTC', 'short', 200.0, 2, 'n', 'abc', True))
        ns = p.parse_args(['trade', 'close', 'perp:BTC'])
        self.assertEqual((ns.what, ns.market, ns.size, ns.client_id, ns.yes), ('close', 'perp:BTC', None, None, False))
        self.assertEqual(p.parse_args(['trade', 'limits']).what, 'limits')
        for bad in (['trade', 'place', 'perp:BTC', 'buy', '200'], ['trade', 'place', 'perp:BTC', 'long'], ['trade', 'close', 'perp:BTC', '--leverage', '2']):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                p.parse_args(bad)

    def test_limits_prints_the_status_and_what_was_placed(self):
        self.app = App({STATUS: status()}, scopes=SCOPES)
        code, out, err = self.cli('trade', 'limits')
        self.assertEqual((code, self.app.read), (0, [STATUS]))
        for said in ('Agent trading: active  (Hyperliquid perpetuals, wallet 0x12…abcd)',
                     'limits: $500.00 per order, $2,000.00 per day, leverage up to 5x, slippage up to 1.00%, until 2026-11-30T00:00:00+00:00',
                     'today: $350.00 used, $1,650.00 left', 'Recent orders', 'Long BTC  $200.00 at 3x  [filled]', '[not_filled]', 'Did not fill.'):
            self.assertIn(said, out, said)
        self.app = App({STATUS: status(status='not_set_up', limits=None, wallet=None, used_today_usd=None, left_today_usd=None, recent=[])}, scopes=SCOPES)
        code, out, err = self.cli('trade', 'limits')
        self.assertIn('Agent trading: not_set_up  (Hyperliquid perpetuals)', out)
        self.assertIn('No orders placed by this agent yet.', out)
        self.assertNotIn('$0', out)
        for name in ('place_trade', 'get_trading_status'):
            self.assertIn(name, render.VIEWS)


class Documented(unittest.TestCase):
    def read(self, *path):
        with open(os.path.join(HERE, '..', *path), encoding='utf-8') as f:
            return f.read()

    def test_the_readme_says_what_the_app_enforces(self):
        readme = self.read('README.md')
        for said in ('`get_trading_status`', '`place_trade`', '`trades:place`', '`HENCE_MCP_TRADING`', 'the limits the person set', 'Hyperliquid perpetuals',
                     'open to the Hence team only for now', 'the trading key never leaves Hence', 'cannot\nwithdraw', 'hence trade place', 'hence trade close',
                     'hence trade limits'):
            self.assertIn(said, readme, said)

    def test_the_skill_says_when_to_place_and_when_to_propose(self):
        skill = self.read('skills', 'hence', 'SKILL.md')
        for said in ('## Placing or proposing', 'The person asked for this trade', 'Agent trading is active', 'the SAME id', '`unknown`', 'propose instead',
                     'get_trading_status', 'place_trade'):
            self.assertIn(said, skill, said)


if __name__ == '__main__':
    unittest.main()
