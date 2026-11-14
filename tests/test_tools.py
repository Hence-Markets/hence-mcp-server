"""Tool behaviour, and the invariants this slice ships with:

  * every tool is a read (pinned on the registry AND on the source text, so a later change
    that adds a write has to change these tests on purpose);
  * a failed read is an error the model is told about, never an empty or zeroed result;
  * when one of two sources fails, the result says so and is not presented as complete.
"""
import os
import re
import unittest

from gateway import tools
from gateway.upstream import Expired, NotFound, Unavailable

from .support import T1, T2, StubUpstream, fixture, standard_routes

WS = '/api/thesis-workspace'
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'gateway')


class ReadOnlyByConstruction(unittest.TestCase):
    def test_every_tool_declares_itself_a_read(self):
        self.assertEqual([t['name'] for t in tools.TOOLS],
                         ['search_theses', 'get_thesis', 'get_featured_theses', 'search_markets', 'get_asset', 'get_thesis_guide'])
        for t in tools.TOOLS:
            a = t['annotations']
            self.assertIs(a['readOnlyHint'], True, t['name'])
            self.assertIs(a['destructiveHint'], False, t['name'])
            self.assertNotRegex(t['name'], r'(trade|order|buy|sell|withdraw|transfer|send|deposit|fund|create|approve|sign)')

    def test_the_gateway_only_ever_reads_the_app(self):
        # Two modules talk to the network, and each is boxed in:
        #   upstream.py  public reads: a GET with no body, nothing else;
        #   appauth.py   the signed-in half: it may ask the app who a token speaks for (one POST
        #                path) and read the account paths listed in ACCOUNT_PATHS, nothing else.
        # No module holds a user's key or reaches a signing library for money. When a later slice
        # adds a path it adds it to ACCOUNT_PATHS and to this test, on purpose.
        from gateway import appauth
        seen_network = []
        for name in sorted(os.listdir(SRC)):
            if not name.endswith('.py'):
                continue
            with open(os.path.join(SRC, name), encoding='utf-8') as f:
                code = f.read()
            self.assertIsNone(re.search(r"eth_account|private_key|PRIVY_APP_SECRET|_KEY['\"]", code), name)
            self.assertIsNone(re.search(r"^\s*(?:import|from)\s+(?:subprocess|socket|http\.client|requests|httpx|aiohttp)\b", code, re.M), name)
            if 'urllib.request' in code:
                seen_network.append(name)
        self.assertEqual(seen_network, ['appauth.py', 'upstream.py'])
        with open(os.path.join(SRC, 'upstream.py'), encoding='utf-8') as f:
            code = f.read()
        requests_made = re.findall(r"urllib\.request\.Request\((.*?)\)\n", code, re.S)
        self.assertEqual(len(requests_made), 1)
        self.assertNotRegex(requests_made[0], r"\bdata\s*=")       # urllib POSTs only when given data=
        self.assertNotRegex(requests_made[0], r"\bmethod\s*=")
        # the signed-in half reaches exactly these, and every one of them is a read of the person's own account
        self.assertEqual(appauth.INTROSPECT, '/api/agents/v1/introspect')
        self.assertEqual(appauth.ACCOUNT_PATHS, ('/api/agents/v1/whoami', '/api/agents/v1/theses', '/api/agents/v1/portfolio', '/api/agents/v1/research',
                                                 '/api/agents/v1/trades/proposals'))
        self.assertEqual([p.pattern for p in appauth.ACCOUNT_PATTERNS],
                         [r'^/api/agents/v1/theses/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(/performance)?$',
                          r'^/api/agents/v1/research/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
                          r'^/api/agents/v1/trades/proposals/pr_[A-Za-z0-9_-]{22}$'])
        ok = '11111111-1111-4111-8111-111111111111'
        for good in ('/api/agents/v1/whoami', '/api/agents/v1/theses/' + ok, '/api/agents/v1/theses/%s/performance' % ok):
            self.assertTrue(appauth.allowed(good), good)
        for bad in ('/api/me', '/api/exchange', '/api/agents/v1/introspect', '/api/agents/v1/theses/' + ok + '/../../me',
                    '/api/agents/v1/theses/x', '/api/agents/v1/portfolio?wallet=0x1', '/api/agents/v1/theses/%s/tracking' % ok,
                    '/api/agents/v1/whoami/', None, 7):
            self.assertFalse(appauth.allowed(bad), bad)
        with open(os.path.join(SRC, 'appauth.py'), encoding='utf-8') as f:
            code = f.read()
        calls = re.findall(r"self\._call\(([^)]*)\)", code)
        # asking who a token is; SENDING a thesis the agent wrote (two paths, below); reading
        self.assertEqual(calls, ["'POST', INTROSPECT, '', body", "'POST', path, grant, body", "'GET', target, grant, None"])
        # The one thing this server sends under a person's connection is a thesis the agent
        # wrote, to be checked or to be put in their library as a private DRAFT. Neither path
        # creates a thesis, publishes one or touches money. A third path here is a decision.
        # ... and a request for Hence to research an idea: the person's own run, under their own daily limit.
        # ... and a trade PREPARED for the person: words Hence stores, which fill in the person's own
        # order ticket when they open it. The path is a list of proposals, not an order endpoint:
        # the person reviews and signs every trade in Hence (tests/test_trades.py).
        self.assertEqual(appauth.WRITE_PATHS, ('/api/agents/v1/theses/check', '/api/agents/v1/theses/drafts', '/api/agents/v1/research',
                                               '/api/agents/v1/trades/proposals'))
        self.assertIn("if path not in WRITE_PATHS:\n            raise ValueError", code)
        self.assertLess(code.index('if path not in WRITE_PATHS:'), code.index("self._call('POST', path, grant, body)"))
        for path in appauth.WRITE_PATHS:
            # "trades/proposals" is the one place the word may appear: a proposal is not an order
            self.assertNotRegex(path.replace('/trades/proposals', '/proposals'),
                                r'(trade|order|exchange|fill|withdraw|transfer|deposit|fund|sign|publish|visibility)')
        self.assertIn("if not allowed(path):\n            raise ValueError", code)
        self.assertLess(code.index('if not allowed(path):'), code.index("self._call('GET', target, grant, None)"))

    def test_signed_in_tools_read_except_the_one_that_saves_a_draft(self):
        writers = [t['name'] for t in tools.ACCOUNT_TOOLS + tools.RESEARCH_TOOLS + tools.TRADE_TOOLS if t['annotations']['readOnlyHint'] is not True]
        # another tool that changes anything is a decision made here: one saves a draft, one starts the person's own
        # research run, one PREPARES a trade for the person to review and sign in Hence (it places nothing)
        self.assertEqual(writers, ['save_thesis_draft', 'start_thesis_research', 'propose_trade', 'propose_basket'])
        research = tools.RESEARCH_TOOLS[0]
        self.assertEqual((research['scope'], [t['name'] for t in tools.RESEARCH_TOOLS]), ('theses:research', ['start_thesis_research']))
        for said in ('counts toward the person\'s daily research limit', 'ask them first', '20 to 40 minutes', 'get_thesis_guide'):
            self.assertIn(said, research['description'])
        for arg in research['inputSchema']['properties']:
            self.assertNotRegex(arg, r'(?i)wallet|address|user|account|visibility|publish|engine|profile')
        for t in tools.ACCOUNT_TOOLS:
            self.assertIs(t['annotations']['destructiveHint'], False, t['name'])
            self.assertNotRegex(t['name'], r'(trade|order|buy|sell|withdraw|transfer|send|deposit|fund|create|approve|sign|publish)')
            # a permission is a read, or the one that saves drafts
            self.assertRegex(t['scope'], r'^([a-z]+:read|theses:write)$')
            self.assertNotIn('scope', tools.public_definition(t))
        # the tool that writes says what it does not do, and a host is told to ask before running it
        save = tools.find('save_thesis_draft') or next(t for t in tools.ACCOUNT_TOOLS if t['name'] == 'save_thesis_draft')
        self.assertEqual(save['scope'], 'theses:write')
        for said in ('private draft', 'does not create, publish or trade', 'the person has seen the thesis and agreed', 'not by Hence'):
            self.assertIn(said, save['description'])
        self.assertIs(next(t for t in tools.ACCOUNT_TOOLS if t['name'] == 'check_thesis')['annotations']['readOnlyHint'], True)

    def test_the_public_definition_hides_the_handler(self):
        d = tools.public_definition(tools.TOOLS[0])
        self.assertNotIn('handler', d)
        self.assertEqual(d['inputSchema']['additionalProperties'], False)


class SearchTheses(unittest.TestCase):
    def test_a_search_maps_the_filters_and_returns_cards(self):
        up = StubUpstream(standard_routes())
        out = tools.search_theses({'query': 'ai capex', 'basket': 'perps', 'sort': 'latest', 'limit': 2}, up)
        path, q = up.calls[0]
        self.assertEqual(path, WS + '/catalogue')
        self.assertEqual((q['view'], q['venue'], q['q'], q['sort'], q['limit'], q['offset'], q['snapshot']),
                         ('discover', 'perp', 'ai capex', 'latest', 2, 0, None))
        self.assertEqual([t['thesis_id'] for t in out['theses']], [T1, T2])
        self.assertEqual(out['total_matching'], 9)
        self.assertIn('hypothetical', out['note'])
        self.assertNotIn('incomplete', out)

    def test_the_cursor_carries_the_same_selection_to_the_next_page(self):
        up = StubUpstream(standard_routes())
        first = tools.search_theses({'query': 'oil', 'basket': 'spot'}, up)
        tools.search_theses({'cursor': first['next_cursor'], 'query': 'ignored once a cursor is given'}, up)
        _, q = up.calls[1]
        self.assertEqual((q['snapshot'], q['offset'], q['q'], q['venue']), ('snap-fixture-1', 2, 'oil', 'spot'))

    def test_the_last_page_has_no_cursor(self):
        page = fixture('catalogue')
        page['next_offset'] = None
        out = tools.search_theses({}, StubUpstream({WS + '/catalogue': page}))
        self.assertIsNone(out['next_cursor'])

    def test_a_partial_venue_read_is_said_out_loud(self):
        page = fixture('catalogue')
        page['coverage_partial'] = True
        out = tools.search_theses({'basket': 'spot'}, StubUpstream({WS + '/catalogue': page}))
        self.assertIn('may be missing', out['incomplete'])

    def test_failures_are_errors_never_an_empty_list(self):
        for failure, words in ((Unavailable('Hence answered 503', 503), 'No theses were read'),
                               (Expired('x'), 'expired'), (NotFound('x'), 'not open')):
            with self.assertRaises(tools.ToolError) as ctx:
                tools.search_theses({}, StubUpstream({WS + '/catalogue': failure}))
            self.assertIn(words, str(ctx.exception))
        with self.assertRaises(tools.ToolError):                      # an answer with no list is not "no results"
            tools.search_theses({}, StubUpstream({WS + '/catalogue': {'total': 0}}))

    def test_bad_arguments_are_refused_before_any_read(self):
        for bad in ({'limit': 13}, {'limit': 0}, {'limit': True}, {'limit': '6'}, {'basket': 'paper'}, {'sort': 'best'},
                    {'query': 'x' * 201}, {'query': 5}, {'cursor': 'not-a-cursor'}, {'surprise': 1}):
            up = StubUpstream(standard_routes())
            with self.assertRaises(tools.ToolError, msg=repr(bad)):
                tools.search_theses(bad, up)
            self.assertEqual(up.calls, [], repr(bad))

    def test_a_forged_cursor_cannot_smuggle_other_filters(self):
        forged = tools._cursor_pack('snap', 0, {'q': '', 'sort': '', 'venue': 'all', 'author': 'did:privy:someone'})
        with self.assertRaises(tools.ToolError):
            tools.search_theses({'cursor': forged}, StubUpstream(standard_routes()))


class GetThesis(unittest.TestCase):
    def test_a_thesis_with_its_price_history(self):
        up = StubUpstream(standard_routes())
        out = tools.get_thesis({'thesis_id': T1.upper(), 'period_days': 180}, up)
        self.assertEqual([c[0] for c in up.calls], [WS + '/theses/' + T1, WS + '/theses/' + T1 + '/performance'])
        self.assertEqual(up.calls[1][1], {'days': 180, 'live': 1})
        self.assertEqual(out['performance']['period_days'], 180)
        self.assertEqual(out['performance']['return_pct'], 11.36)
        self.assertIn('cannot place trades', out['how_to_trade'])
        self.assertNotIn('related', out)

    def test_a_failed_price_history_keeps_the_thesis_and_forbids_a_figure(self):
        routes = standard_routes()
        routes[WS + '/theses/' + T1 + '/performance'] = Unavailable('Hence answered 502', 502)
        out = tools.get_thesis({'thesis_id': T1}, StubUpstream(routes))
        self.assertIsNone(out['performance'])
        self.assertIn('Do not state a return', out['performance_unavailable'])
        self.assertEqual(out['title'], 'Fixture thesis title')

    def test_performance_and_related_are_only_read_when_wanted(self):
        up = StubUpstream(standard_routes())
        out = tools.get_thesis({'thesis_id': T1, 'include_performance': False, 'include_related': True}, up)
        self.assertEqual([c[0] for c in up.calls], [WS + '/theses/' + T1, WS + '/theses/' + T1 + '/related'])
        self.assertNotIn('performance', out)
        self.assertEqual([r['thesis_id'] for r in out['related']], [T2])     # the row with a bad id is dropped

    def test_a_private_or_missing_thesis_is_one_answer(self):
        with self.assertRaises(tools.ToolError) as ctx:
            tools.get_thesis({'thesis_id': T2}, StubUpstream({WS + '/theses/' + T2: NotFound('x')}))
        self.assertIn('no public thesis', str(ctx.exception))

    def test_an_id_that_is_not_an_id_never_reaches_the_app(self):
        for bad in ('', 'abc', '../../me', T1 + '/positions', T1 + '?x=1', 5, None):
            up = StubUpstream(standard_routes())
            with self.assertRaises(tools.ToolError, msg=repr(bad)):
                tools.get_thesis({'thesis_id': bad}, up)
            self.assertEqual(up.calls, [], repr(bad))

    def test_an_answer_without_a_body_is_not_a_thesis(self):
        with self.assertRaises(tools.ToolError):
            tools.get_thesis({'thesis_id': T1}, StubUpstream({WS + '/theses/' + T1: {'id': T1}}))


class Featured(unittest.TestCase):
    def test_todays_selection(self):
        out = tools.get_featured_theses({'limit': 1}, StubUpstream(standard_routes()))
        self.assertEqual(len(out['theses']), 1)
        self.assertIn('says nothing about what comes next', out['selection_rule'])
        self.assertTrue(out['topics'])

    def test_a_topic_filter_and_a_warming_edition(self):
        data = fixture('features')
        data['warming'] = True
        topic = data['candidates'][1]['topic']
        out = tools.get_featured_theses({'topic': topic.upper()}, StubUpstream({WS + '/discovery/features': data}))
        self.assertTrue(all(t['topic'] == topic for t in out['theses']))
        self.assertIn('still being prepared', out['incomplete'])

    def test_a_failed_read_is_an_error(self):
        with self.assertRaises(tools.ToolError):
            tools.get_featured_theses({}, StubUpstream({WS + '/discovery/features': Unavailable('Hence could not be reached')}))
        with self.assertRaises(tools.ToolError):
            tools.get_featured_theses({}, StubUpstream({WS + '/discovery/features': {'edition': 'x'}}))


class SearchMarkets(unittest.TestCase):
    def setUp(self):
        tools._NAMES = {'io:OAI': {'name': 'OpenAI', 'asset_class': 'preipo'}}

    def tearDown(self):
        tools._NAMES = None

    def test_a_ticker_finds_its_perps_and_its_token(self):
        out = tools.search_markets({'query': 'nvda'}, StubUpstream(standard_routes()))
        ids = [m['market_id'] for m in out['markets']]
        self.assertEqual(set(ids[:2]), {'perp:xyz:NVDA', 'perp:io:NVDA'})
        self.assertTrue(any(i.startswith('spot:4663:') for i in ids))
        self.assertNotIn('incomplete', out)
        self.assertEqual(out['matches'], 3)

    def test_a_name_finds_a_contract_whose_ticker_is_not_its_name(self):
        out = tools.search_markets({'query': 'openai', 'kind': 'perp'}, StubUpstream(standard_routes()))
        self.assertEqual(out['markets'][0]['market_id'], 'perp:io:OAI')

    def test_no_query_lists_perps_by_volume_and_does_not_read_spot(self):
        up = StubUpstream(standard_routes())
        out = tools.search_markets({'limit': 2}, up)
        self.assertEqual([c[0] for c in up.calls], ['/api/screener/hl'])
        vols = [m['volume_24h_usd'] for m in out['markets']]
        self.assertEqual(vols, sorted(vols, reverse=True))

    def test_one_source_down_is_a_partial_result_that_says_so(self):
        routes = standard_routes()
        routes['/api/screener/spot'] = Unavailable('Hence answered 503', 503)
        out = tools.search_markets({'query': 'nvda'}, StubUpstream(routes))
        self.assertIn('spot could not be read', out['incomplete'])
        self.assertTrue(all(m['kind'] == 'perp' for m in out['markets']))

    def test_every_wanted_source_down_is_an_error_not_no_matches(self):
        routes = {'/api/screener/hl': Unavailable('Hence answered 503', 503), '/api/screener/spot': Unavailable('Hence could not be reached')}
        with self.assertRaises(tools.ToolError) as ctx:
            tools.search_markets({'query': 'nvda'}, StubUpstream(routes))
        self.assertIn('No markets were read', str(ctx.exception))
        with self.assertRaises(tools.ToolError):
            tools.search_markets({'kind': 'perp'}, StubUpstream(routes))

    def test_a_payload_of_the_wrong_shape_is_a_failed_read(self):
        with self.assertRaises(tools.ToolError):
            tools.search_markets({'kind': 'perp'}, StubUpstream({'/api/screener/hl': {'rows': []}}))

    def test_no_match_is_an_honest_empty_list(self):
        out = tools.search_markets({'query': 'zzzzzz'}, StubUpstream(standard_routes()))
        self.assertEqual((out['markets'], out['matches']), ([], 0))
        self.assertNotIn('incomplete', out)


if __name__ == '__main__':
    unittest.main()
