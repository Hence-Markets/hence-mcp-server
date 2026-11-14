"""A trade prepared for the person to review and sign in Hence: propose_trade,
get_trade_proposals, `hence trade`, and the card.

What these pin, in the order a mistake would hurt:
  * nothing here places an order, and nothing could: the tools send words to one path of the
    app and read them back; no tool, argument or module names a wallet, a key or a signature;
  * a trade is never reported as made unless the app said "placed" AND sent the order; an
    answer that cannot be read is an error, never "prepared" and never "placed";
  * the tools exist only behind their own switch and their own permission, and the one that
    writes is marked so a host asks the person first;
  * the agent is told, in the answer itself, to say "waiting for you in Hence" and not "done".
"""
import io
import json
import os
import re
import tempfile
import unittest
from unittest import mock

from gateway import appauth, cards, config, protocol, shape, tools
from hence_cli import main, render
from hence_cli.store import Store

from .support import StubUpstream, standard_routes
from .test_account import GRANT, ON, TOKEN, modern
from .test_research import App

BASE = '/api/agents/v1/trades/proposals'
PID = 'pr_' + 'a' * 22
URL = 'https://app.hence.markets/#/proposal/' + PID
ALL = ['account:read', 'portfolio:read', 'trades:propose']
ASK = {'market_id': 'perp:xyz:NVDA', 'side': 'long', 'size_usd': 200, 'leverage': 3}
FEE = {'max_percent': 0.01, 'note': 'Hence adds its fee to the order when it is signed: at most 0.01% of the position\'s value, shown in the ticket before signing. '
                                    'The venue charges its own trading fees on top.'}
HERE = os.path.dirname(os.path.abspath(__file__))


def proposal(**over):
    p = {'proposal_id': PID, 'status': 'waiting', 'market_id': 'perp:xyz:NVDA', 'market': 'NVDA', 'side': 'long', 'size_usd': 200.0, 'leverage': 3,
         'order_type': 'market', 'limit_price': None, 'note': 'Earnings on Wednesday.', 'created_at': '2026-11-14T11:00:00+00:00',
         'expires_at': '2026-11-15T11:00:00+00:00', 'url': URL,
         'what_now': 'Waiting for the person. Give them the link: they review it in Hence and sign it there. Nothing has been placed.'}
    p.update(over)
    return p


def placed(state='filled', confirmed=False, **over):
    return proposal(status='placed', what_now='The person placed the order.',
                    order={'state': state, 'size_usd': 150.0, 'confirmed_by_venue': confirmed, 'placed_at': '2026-11-14T11:30:00+00:00'}, **over)


PROPOSED = {'ok': True, 'proposed': True, 'already_proposed': False, 'problems': [], 'warnings': [], 'proposal': proposal(), 'hence_fee': FEE}


class Case(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, dict(ON, HENCE_MCP_TRADES='1'))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.up = StubUpstream(standard_routes())

    def call(self, name, arguments=None, app=None, signed_in=True):
        headers = {'authorization': 'Bearer ' + TOKEN} if signed_in else None
        status, out, extra = protocol.handle_full(*modern('tools/call', {'name': name, 'arguments': arguments or {}}, headers, name), self.up, auth=app)
        self.status, self.headers = status, extra or {}
        return out if not isinstance(out, dict) or 'result' not in out else out['result']

    def value(self, *a, **k):
        result = self.call(*a, **k)
        self.assertEqual((self.status, result['isError']), (200, False), result)
        return result['structuredContent']

    def refusal(self, *a, **k):
        result = self.call(*a, **k)
        self.assertEqual(self.status, 200)
        self.assertIs(result['isError'], True)
        self.assertNotIn('structuredContent', result)
        return result['content'][0]['text']


class NothingHerePlacesAnOrder(Case):
    def test_the_tools_send_words_to_one_path_and_nothing_else(self):
        self.assertEqual([t['name'] for t in tools.TRADE_TOOLS], ['propose_trade', 'get_trade_proposals'])
        self.assertEqual([t['scope'] for t in tools.TRADE_TOOLS], ['trades:propose', 'trades:propose'])
        # the one that writes is marked so a host asks the person; the other is a read
        self.assertEqual([t['annotations']['readOnlyHint'] for t in tools.TRADE_TOOLS], [False, True])
        for t in tools.TRADE_TOOLS:
            self.assertIs(t['annotations']['destructiveHint'], False)
            self.assertNotRegex(t['name'], r'(place|execute|order|buy|sell|submit|sign|withdraw|transfer|send|deposit|fund|approve)')
        # no argument can name whose money, which wallet, or anything a signature needs
        self.assertEqual(sorted(tools.PROPOSE_TRADE['properties']),
                         ['leverage', 'limit_price', 'market_id', 'note', 'order_type', 'side', 'size_usd', 'valid_for_hours'])
        for arg in list(tools.PROPOSE_TRADE['properties']) + list(tools.GET_PROPOSALS['properties']):
            self.assertNotRegex(arg, r'(?i)wallet|address|user|account|key|signature|nonce|reduce|slippage|approve|auto')
        self.assertIs(tools.PROPOSE_TRADE['additionalProperties'], False)
        self.assertIn(BASE, appauth.WRITE_PATHS)
        self.assertTrue(appauth.allowed(BASE))
        self.assertTrue(appauth.allowed(BASE + '/' + PID))
        for bad in (BASE + '/' + PID + '/open', BASE + '/' + PID + '/dismiss', BASE + '/pr_short', BASE + '/../../exchange',
                    '/api/me/proposals', '/api/me/proposals/%s/open' % PID, '/api/exchange', '/api/me/fills'):
            self.assertFalse(appauth.allowed(bad), bad)
            self.assertNotIn(bad, appauth.WRITE_PATHS)

    def test_no_module_can_reach_a_venue_or_a_key(self):
        for folder in ('gateway', 'hence_cli'):
            for name in sorted(os.listdir(os.path.join(HERE, '..', folder))):
                if not name.endswith('.py'):
                    continue
                with open(os.path.join(HERE, '..', folder, name), encoding='utf-8') as f:
                    code = f.read()
                self.assertIsNone(re.search(r'eth_account|private_key|hyperliquid\.xyz|api\.hyperliquid|/exchange\b|sign_typed|sign_message', code), name)

    def test_the_description_says_what_it_is_and_is_not(self):
        said = tools.TRADE_TOOLS[0]['description']
        for part in ('does NOT place the trade', 'you cannot place one', 'review and sign it with their own wallet', 'Never say the trade was made',
                     'asked for this trade or agreed to it', 'search_markets first', 'not the margin', 'hence_fee'):
            self.assertIn(part, said, part)
        reading = tools.TRADE_TOOLS[1]['description']
        self.assertIn('Only `placed` means an order exists', reading)
        self.assertIn('may differ from what you proposed', reading)


class Proposing(Case):
    def test_a_proposal_is_sent_as_given_and_answered_with_the_link_and_what_to_say(self):
        app = App({BASE: PROPOSED}, scopes=ALL)
        got = self.value('propose_trade', dict(ASK, note='Earnings on Wednesday.'), app)
        self.assertEqual(app.sent, [(BASE, dict(ASK, note='Earnings on Wednesday.'))])
        self.assertEqual((got['proposed'], got['already_proposed'], got['proposal']['proposal_id'], got['proposal']['status'], got['proposal']['url']),
                         (True, False, PID, 'waiting', URL))
        self.assertIs(got['proposal']['placed'], False)
        self.assertNotIn('order', got['proposal'])
        self.assertEqual(got['hence_fee'], FEE)
        for said in ('PREPARED, not placed', 'Give the person the url', 'review and sign it there with their own wallet', 'Do not say the trade was made'):
            self.assertIn(said, got['next'], said)

    def test_it_is_not_offered_or_callable_without_its_switch(self):
        app = App({BASE: PROPOSED}, scopes=ALL)
        self.assertTrue(config.trades_active())
        self.assertIsNotNone(tools.find('propose_trade'))
        for off in ({'HENCE_MCP_TRADES': '0'}, {'HENCE_MCP_TRADES': 'true'}, {'HENCE_MCP_ACCOUNTS': '0'}):
            with mock.patch.dict(os.environ, off):
                self.assertFalse(config.trades_active(), off)
                self.assertIsNone(tools.find('propose_trade'))
                self.assertIsNone(tools.find('get_trade_proposals'))
                self.assertNotIn('trades:propose', {t.get('scope') for t in tools.listed()})       # so sign-in never asks for it
                for name in ('propose_trade', 'get_trade_proposals'):
                    out = self.call(name, ASK if name == 'propose_trade' else {}, app)
                    self.assertIn('Unknown tool', out['error']['message'])
        self.assertEqual((app.sent, app.read), ([], []))

    def test_it_needs_its_own_permission(self):
        app = App({BASE: PROPOSED}, scopes=['account:read', 'portfolio:read', 'theses:write'])
        for name, args in (('propose_trade', ASK), ('get_trade_proposals', {})):
            self.call(name, args, app)
            self.assertEqual(self.status, 403, name)
            self.assertIn('scope="trades:propose"', self.headers['WWW-Authenticate'])
        self.assertEqual((app.sent, app.read), ([], []))
        self.call('propose_trade', ASK, app, signed_in=False)
        self.assertEqual(self.status, 401)

    def test_problems_warnings_and_limits_reach_the_agent(self):
        bad = {'ok': False, 'proposed': False, 'warnings': [], 'problems': [{'field': 'market_id', 'problem': 'Hence does not list a perpetual "xyz:NOPE".'}]}
        got = self.value('propose_trade', dict(ASK, market_id='perp:xyz:NOPE'), App({BASE: bad}, scopes=ALL))
        self.assertEqual((got['proposed'], got['problems'][0]['field']), (False, 'market_id'))
        self.assertIn('Nothing was proposed', got['next'])
        self.assertNotIn('proposal', got)
        warned = dict(PROPOSED, warnings=['10× leverage: a small move against the position can close it at a loss of the whole margin.'],
                      already_proposed=True)
        got = self.value('propose_trade', dict(ASK, leverage=10), App({BASE: warned}, scopes=ALL))
        self.assertEqual((got['already_proposed'], len(got['warnings'])), (True, 1))
        self.assertIn('Tell them each warning', got['next'])
        said = 'This person already has 10 proposed trades waiting in Hence. They can dismiss some there. Nothing was proposed.'
        self.assertEqual(self.refusal('propose_trade', ASK, App({BASE: appauth.Refused(409, 'limit_reached', said)}, scopes=ALL)), said)

    def test_an_unreadable_answer_is_never_prepared(self):
        for broken in ({}, {'ok': True, 'proposed': True}, {'ok': True, 'proposed': True, 'proposal': {'proposal_id': 'x'}},
                       {'ok': True, 'proposed': False, 'proposal': proposal()},
                       {'ok': False, 'proposed': True, 'problems': [{'field': 'x', 'problem': 'y'}]}, {'ok': False, 'proposed': False, 'problems': []},
                       dict(PROPOSED, proposal=proposal(side='up')), dict(PROPOSED, proposal=proposal(size_usd=None)), dict(PROPOSED, proposal=proposal(url=None)),
                       dict(PROPOSED, proposal=placed()),                       # a new proposal cannot already be placed
                       dict(PROPOSED, proposal=proposal(status='expired')), 'text', 7):
            text = self.refusal('propose_trade', ASK, App({BASE: broken}, scopes=ALL))
            self.assertIn('could not be read', text, repr(broken)[:70])
            self.assertIn('the same request reaches the same proposal', text)
        self.assertIn('Unknown argument', self.refusal('propose_trade', dict(ASK, wallet='0x1'), App({BASE: PROPOSED}, scopes=ALL)))
        self.assertIn('Unknown argument', self.refusal('propose_trade', dict(ASK, reduce_only=True), App({BASE: PROPOSED}, scopes=ALL)))


class Following(Case):
    def test_only_the_apps_placed_with_its_order_is_a_trade(self):
        for status in ('waiting', 'opened', 'dismissed', 'expired', 'withdrawn'):
            got = self.value('get_trade_proposals', {'proposal_id': PID}, App({BASE + '/' + PID: proposal(status=status)}, scopes=ALL))
            self.assertEqual((got['status'], got['placed']), (status, False))
            self.assertNotIn('order', got)
        got = self.value('get_trade_proposals', {'proposal_id': PID}, App({BASE + '/' + PID: placed('filled', True)}, scopes=ALL))
        self.assertEqual(got['order'], {'state': 'filled', 'size_usd': 150.0, 'confirmed_by_venue': True, 'placed_at': '2026-11-14T11:30:00+00:00'})
        self.assertNotIn('placed', got)
        unconfirmed = self.value('get_trade_proposals', {'proposal_id': PID}, App({BASE + '/' + PID: placed('filled', False)}, scopes=ALL))
        self.assertIs(unconfirmed['order']['confirmed_by_venue'], False)

    def test_status_and_order_must_agree(self):
        broken = (proposal(status='placed'),                                   # placed, no order
                  proposal(order={'state': 'filled', 'confirmed_by_venue': True}),    # an order on a proposal that is not placed
                  placed(state='cancelled'), placed(confirmed='yes'), proposal(status='filled'), proposal(proposal_id='pr_x'),
                  proposal(what_now=None), proposal(market_id=None), {}, None, 'x')
        for row in broken:
            self.assertIsNone(shape.trade_proposal(row), repr(row)[:70])
            self.assertIn('could not be read', self.refusal('get_trade_proposals', {'proposal_id': PID}, App({BASE + '/' + PID: row}, scopes=ALL)))

    def test_the_list_and_a_bad_id(self):
        rows = {'proposals': [proposal(), placed('resting')]}
        got = self.value('get_trade_proposals', {}, App({BASE: rows}, scopes=ALL))
        self.assertEqual([p['status'] for p in got['proposals']], ['waiting', 'placed'])
        self.assertEqual(self.value('get_trade_proposals', {}, App({BASE: {'proposals': []}}, scopes=ALL)), {'proposals': []})
        # one unreadable row: the list is not shown as a complete one
        self.assertIn('could not be read', self.refusal('get_trade_proposals', {}, App({BASE: {'proposals': [proposal(), proposal(side=None)]}}, scopes=ALL)))
        self.assertIn('could not be read', self.refusal('get_trade_proposals', {}, App({BASE: {}}, scopes=ALL)))
        app = App(scopes=ALL)
        for bad in ('../portfolio', 'pr_short', PID + '/open', '99999999-9999-4999-8999-999999999999'):
            self.assertIn('not a proposal id', self.refusal('get_trade_proposals', {'proposal_id': bad}, app), bad)
        self.assertEqual(app.read, [])
        gone = App({BASE + '/' + PID: appauth.Refused(404, 'not_found', 'This connection has no proposal with that id.')}, scopes=ALL)
        self.assertEqual(self.refusal('get_trade_proposals', {'proposal_id': PID}, gone), 'This connection has no proposal with that id.')


class Card(Case):
    def test_the_card_draws_a_prepared_trade_and_never_calls_it_made(self):
        with open(os.path.join(HERE, '..', 'data', 'thesis-card.html'), encoding='utf-8') as f:
            html = f.read()
        self.assertIn('propose_trade', cards.TOOLS)
        self.assertIn('get_trade_proposals', cards.TOOLS)
        self.assertEqual(tools.public_definition(tools.TRADE_TOOLS[0])['_meta'], {'ui': {'resourceUri': 'ui://hence/thesis-card'}})
        for said in ("'Prepared trade · not placed'", "'Review and sign in Hence'", 'Nothing is placed until you sign it there.',
                     "waiting: 'Waiting for you in Hence. Nothing has been placed.'", "'Nothing was prepared'",
                     "p.order.confirmed_by_venue === true ? 'You placed this order and it filled.' : 'You placed this order. Hence is confirming the fill with the venue.'"):
            self.assertIn(said, html, said)
        # the button is offered only while it can still be opened, and it only opens a link
        self.assertIn("var open = p.status === 'waiting' || p.status === 'opened';", html)
        self.assertNotRegex(html, r"request\('(tools/call|ui/message|ui/update-model-context)'")
        # every field the card reads is one the tools return
        got = shape.trade_propose(PROPOSED)
        for field in ('status', 'side', 'market', 'market_id', 'size_usd', 'leverage', 'order_type', 'limit_price', 'note', 'url'):
            self.assertIn(field, got['proposal'], field)
            self.assertIn('p.' + field, html, field)
        self.assertIn('answer.hence_fee.note', html)
        self.assertIn("p.order_type === 'limit' ? 'Limit ' + px(p.limit_price) : 'Market'", html)        # a limit price shows its cents, or n/a
        self.assertIn('answer.warnings', html)
        self.assertIn('value.proposals', html)


class Command(unittest.TestCase):
    """`hence trade`, with the server's own tool functions behind it."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = Store(os.path.join(self.dir.name, 'credentials.json'))
        self.store.update('https://mcp.hence.markets/mcp', access_token='hat_x', expires_at=None)
        self.app = None

    def sender(self, url, method, headers, body, timeout):
        msg = json.loads(body)
        name, args = msg['params']['name'], msg['params']['arguments']

        class Account:
            def __init__(account, app):
                account.app = app

            def get(account, path, query=None):
                return account.app.get(GRANT, path, query)

            def post(account, path, payload):
                return account.app.post(GRANT, path, payload)
        try:
            value = (tools.propose_trade if name == 'propose_trade' else tools.get_trade_proposals)(args, None, Account(self.app))
            result = {'content': [{'type': 'text', 'text': json.dumps(value)}], 'structuredContent': value, 'isError': False}
        except (appauth.Unavailable, appauth.Refused, tools.ToolError) as e:
            result = {'content': [{'type': 'text', 'text': str(e)}], 'isError': True}
        return 200, json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': result}).encode(), {}

    def cli(self, *argv, confirm=None):
        out, err = io.StringIO(), io.StringIO()
        code = main.run(list(argv), out=out, err=err, store=self.store, sender=self.sender, confirm=confirm)
        return code, out.getvalue(), err.getvalue()

    def test_propose_asks_first_and_no_sends_nothing(self):
        self.app = App({BASE: PROPOSED}, scopes=ALL)
        code, out, err = self.cli('trade', 'propose', 'perp:xyz:NVDA', 'long', '200', '--leverage', '3', confirm=lambda: False)
        self.assertEqual((code, out, self.app.sent), (1, '', []))
        self.assertIn('Prepare for review in Hence: long perp:xyz:NVDA, $200 at 3x, market order', err)
        self.assertIn('Not sent', err)

    def test_propose_sends_what_was_typed_and_prints_the_link_and_not_placed(self):
        self.app = App({BASE: PROPOSED}, scopes=ALL)
        code, out, err = self.cli('trade', 'propose', 'perp:xyz:NVDA', 'long', '200', '--leverage', '3', '--note', 'Earnings on Wednesday.', '--yes')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.app.sent, [(BASE, dict(ASK, note='Earnings on Wednesday.'))])
        for said in ('Long NVDA  $200.00 at 3x  market order  [waiting]', 'Not placed.', 'review and sign in Hence: ' + URL, 'at most 0.01%'):
            self.assertIn(said, out, said)
        self.assertNotRegex(out, r'(?i)\b(filled|executed|bought|trade placed|order placed)\b')
        # a limit order, a size with cents, and how long it stays open
        self.app = App({BASE: PROPOSED}, scopes=ALL)
        self.cli('trade', 'propose', 'perp:BTC', 'short', '1250.5', '--limit', '70000', '--valid-hours', '6', '--yes')
        self.assertEqual(self.app.sent, [(BASE, {'market_id': 'perp:BTC', 'side': 'short', 'size_usd': 1250.5, 'order_type': 'limit', 'limit_price': 70000.0,
                                                 'valid_for_hours': 6})])

    def test_problems_exit_1_and_say_nothing_was_prepared(self):
        self.app = App({BASE: {'ok': False, 'proposed': False, 'warnings': [], 'problems': [{'field': 'market_id', 'problem': 'Hence does not list it.'}]}}, scopes=ALL)
        code, out, err = self.cli('trade', 'propose', 'perp:xyz:NOPE', 'long', '200', '--yes')
        self.assertEqual(code, 1)
        self.assertIn('1 problem. Nothing was prepared.', out)
        self.assertIn('market_id: Hence does not list it.', out)

    def test_status_takes_an_id_or_a_link_and_says_what_the_app_said(self):
        self.app = App({BASE + '/' + PID: placed('filled', True)}, scopes=ALL)
        code, out, err = self.cli('trade', 'status', URL)
        self.assertEqual((code, self.app.read), (0, [BASE + '/' + PID]))
        self.assertIn('[placed]', out)
        self.assertIn('order: filled, $150.00, confirmed by the venue', out)
        self.assertNotIn('Not placed.', out)
        self.assertNotIn('review and sign', out)                       # nothing left to sign: the link is only where it is shown
        self.assertIn('in Hence: ' + URL, out)
        self.app = App({BASE + '/' + PID: placed('filled', False)}, scopes=ALL)
        self.assertIn('not yet confirmed by the venue', self.cli('trade', 'status', PID)[1])
        code, out, err = self.cli('trade', 'status', 'nope')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('is not a proposal id', err)

    def test_list_and_the_views_read_what_the_server_sends(self):
        self.app = App({BASE: {'proposals': [proposal(), placed('resting', size_usd=1500.0, leverage=None, order_type='limit', limit_price=139.5)]}}, scopes=ALL)
        code, out, err = self.cli('trade', 'list')
        self.assertEqual(code, 0, err)
        self.assertIn('Long NVDA  $200.00 at 3x  market order  [waiting]', out)
        self.assertIn('Long NVDA  $1,500.00  limit 139.50  [placed]', out)             # exact to the cent, never abbreviated
        self.assertIn('order: resting, $150.00, not yet confirmed by the venue', out)
        self.app = App({BASE: {'proposals': []}}, scopes=ALL)
        self.assertIn('No trades have been prepared', self.cli('trade', 'list')[1])
        for name in ('propose_trade', 'get_trade_proposals'):
            self.assertIn(name, render.VIEWS)
        code, out, err = self.cli('trade')
        self.assertEqual(code, 1)
        self.assertIn('hence trade propose', err)


if __name__ == '__main__':
    unittest.main()
