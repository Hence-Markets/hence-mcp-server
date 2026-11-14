"""The fuller portfolio: the Hyperliquid account by its mode, outcome positions, and spot
balances on request (get_my_portfolio, `hence portfolio --spot`).

What these pin: the sections are passed on as the app computed them, by allowlist; a section
the app did not send stays absent and one it could not read stays null; spot balances are
asked for only when the caller says so; and nothing here adds the parts into one total.
"""
import io
import json
import unittest

from gateway import shape, tools
from hence_cli import main, render

from .test_account import GRANT
from .test_my_account import BASE, PORTFOLIO, App

ACCOUNT = {'wallets': [{'wallet': '0x1111…1111', 'mode': 'unified', 'value_usd': 510.0, 'cash_available_usd': 400.0, 'withdrawable_usd': 400.0, 'unread': [],
                        'internal': 'must not matter'},
                       {'wallet': '0x2222…2222', 'mode': None, 'value_usd': None, 'cash_available_usd': None, 'withdrawable_usd': None, 'unread': ['account mode']}],
           'total_value_usd': None, 'total_cash_available_usd': None, 'complete': False, 'matches': 'the Perps figure in the Hence wallet chip and Wallet sheet',
           'note': 'Hyperliquid only.', 'debug': 'x'}
OUTCOMES = {'positions': [{'wallet': '0x1111…1111', 'market': 'Fed cuts in December', 'side': 'No', 'contracts': 10.0, 'price': 0.4, 'value_usd': 4.0},
                          {'wallet': '0x1111…1111', 'market': 'Outcome 9999', 'side': 'Yes', 'contracts': 3.0, 'price': None, 'value_usd': None}],
            'complete': True, 'note': 'Hyperliquid outcome markets.'}
SPOT = {'wallets': [{'wallet': '0x1111…1111', 'tokens': [{'symbol': 'NVDA', 'chain': 'Robinhood Chain', 'amount': 2.0, 'price': 140.0, 'value_usd': 280.0}],
                     'tokens_partial': False, 'stablecoins': None}],
        'tokens_value_usd': 280.0, 'stablecoins_value_usd': None, 'complete': False, 'wallets_owned': 1, 'wallets_read': 1, 'matches': 'the Spot holdings', 'note': 'x'}
FULL = dict(PORTFOLIO, account=ACCOUNT, outcomes=OUTCOMES)


class Account:
    def __init__(self, app):
        self.app = app

    def get(self, path, query=None):
        return self.app.get(GRANT, path, query)


class Shape(unittest.TestCase):
    def test_the_new_sections_are_passed_on_by_allowlist(self):
        got = shape.portfolio(FULL)
        self.assertEqual(sorted(got['account']), ['complete', 'matches', 'note', 'total_cash_available_usd', 'total_value_usd', 'wallets'])
        self.assertEqual((got['account']['total_value_usd'], got['account']['complete'], got['account']['wallets'][1]['unread']), (None, False, ['account mode']))
        self.assertEqual(got['outcome_positions']['positions'][1]['value_usd'], None)            # not priced is null, never 0
        self.assertNotIn('debug', got['account'])
        self.assertNotIn('spot', got)                                                           # not asked for: absent, not empty
        self.assertEqual(shape.portfolio(dict(FULL, spot=SPOT))['spot']['tokens_value_usd'], 280.0)
        # an older app that sends neither section still answers; they are null, not invented
        old = shape.portfolio(PORTFOLIO)
        self.assertEqual((old['account'], old['outcome_positions']), (None, None))
        for said in ('never add its parts into one total', 'is null unless every wallet was read', 'was not priced: it is not zero'):
            self.assertIn(said, got['note'], said)

    def test_nothing_here_adds_the_parts_up(self):
        got = shape.portfolio(dict(FULL, spot=SPOT))
        for key in ('total', 'net_worth', 'portfolio_value', 'total_usd'):
            self.assertNotIn(key, got)


class Tool(unittest.TestCase):
    def test_spot_is_asked_for_only_when_the_caller_says_so(self):
        app = App({BASE + '/portfolio': FULL})
        tools.get_my_portfolio({}, None, Account(app))
        tools.get_my_portfolio({'history_days': 90, 'include_spot': False}, None, Account(app))
        tools.get_my_portfolio({'include_spot': True}, None, Account(App({BASE + '/portfolio': dict(FULL, spot=SPOT)})))
        self.assertEqual([q for _, _, q in app.read], [{'days': 30}, {'days': 90}])
        asked = App({BASE + '/portfolio': dict(FULL, spot=SPOT)})
        got = tools.get_my_portfolio({'include_spot': True}, None, Account(asked))
        self.assertEqual(asked.read, [(GRANT, BASE + '/portfolio', {'days': 30, 'include': 'spot'})])
        self.assertEqual(got['spot']['wallets'][0]['stablecoins'], None)                        # that half could not be read
        with self.assertRaises(tools.ToolError):
            tools.get_my_portfolio({'include_spot': 'yes'}, None, Account(asked))
        tool = next(t for t in tools.ACCOUNT_TOOLS if t['name'] == 'get_my_portfolio')
        for said in ('account value and available cash', 'outcome-market positions', 'include_spot', 'must not be added together', 'never zero'):
            self.assertIn(said, tool['description'], said)
        self.assertEqual(sorted(tools.GET_MY_PORTFOLIO['properties']), ['history_days', 'include_spot'])


class Command(unittest.TestCase):
    def cli(self, value, *argv):
        def sender(url, method, headers, body, timeout):
            self.sent = json.loads(body)['params']['arguments']
            return 200, json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {'content': [{'type': 'text', 'text': json.dumps(value)}],
                                                                           'structuredContent': value, 'isError': False}}).encode(), {}
        import os, tempfile
        from hence_cli.store import Store
        folder = tempfile.mkdtemp()
        store = Store(os.path.join(folder, 'credentials.json'))
        store.update('https://mcp.hence.markets/mcp', access_token='hat_x', expires_at=None)
        out, err = io.StringIO(), io.StringIO()
        code = main.run(list(argv), out=out, err=err, sender=sender, store=store)
        return code, out.getvalue(), err.getvalue()

    def test_the_view_says_what_was_read_and_what_was_not(self):
        code, out, err = self.cli(shape.portfolio(dict(FULL, spot=SPOT)), 'portfolio', '--spot')
        self.assertEqual((code, self.sent), (0, {'include_spot': True}), err)
        for said in ('Hyperliquid account', '0x1111…1111', 'unified', '$510.00', '$400.00', 'could not read account mode',
                     'total value: n/a', 'a total is stated only when every wallet was read',
                     'Outcome positions', 'Fed cuts in December', '$4.00', 'Spot, wallet 0x1111…1111', 'NVDA', '$280.00',
                     'stablecoins: n/a (could not be read)', '(not complete)'):
            self.assertIn(said, out, said)
        code, out, err = self.cli(shape.portfolio(PORTFOLIO), 'portfolio')
        self.assertEqual(self.sent, {})
        self.assertNotIn('\nHyperliquid account\n', out)                                       # a section the app did not send is not drawn empty
        self.assertNotIn('Spot, wallet', out)
        self.assertIn('get_my_portfolio', render.VIEWS)


if __name__ == '__main__':
    unittest.main()
