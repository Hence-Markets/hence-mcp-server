"""The mappers' promises: a missing figure is None (never 0), nothing is invented, account ids
never leave, and thesis text passes through as labelled content."""
import json
import unittest

from gateway import shape

from .support import T1, fixture

BASE = 'https://app.example'


class Figures(unittest.TestCase):
    def test_a_missing_or_broken_figure_is_none_never_zero(self):
        for bad in (None, '', '12', 'n/a', float('nan'), float('inf'), True, False, [], {}):
            self.assertIsNone(shape.num(bad), repr(bad))
        self.assertEqual(shape.num(0), 0)          # a real zero stays a zero
        self.assertEqual(shape.num(1.23456, 2), 1.23)

    def test_text_unwraps_a_json_object_in_a_string(self):
        self.assertEqual(shape.text('{"date": "2026-11-08", "text": "Because."}'), 'Because.')
        self.assertEqual(shape.text('{"hindsight_risk": "Risk one."}'), 'Risk one.')
        self.assertEqual(shape.text('  plain  '), 'plain')
        for empty in (None, '', '   ', 5, {}):
            self.assertIsNone(shape.text(empty), repr(empty))


class Thesis(unittest.TestCase):
    def setUp(self):
        self.out = shape.thesis_detail(fixture('thesis'), BASE)

    def test_the_claim_and_baskets_survive(self):
        o = self.out
        self.assertEqual(o['thesis_id'], T1)
        self.assertEqual(o['why_now'], 'Fixture why-now sentence.')
        self.assertEqual(o['counter_case'], 'Fixture counter case.')
        self.assertEqual([h['weight_pct'] for h in o['holdings']], [50.0, 30.0, 20.0])
        self.assertEqual(sum(h['weight_pct'] for h in o['holdings']), 100.0)
        self.assertEqual(o['other_baskets'][0]['venue'], 'Solana')
        self.assertEqual(len(o['other_baskets'][0]['holdings']), 2)
        self.assertEqual(o['url'], BASE + '/t/' + T1)
        self.assertEqual(o['upkeep'], {'mode': 'scheduled', 'check_every_hours': 24.0, 'rule': 'Fixture upkeep rule.'})

    def test_account_ids_never_leave(self):
        blob = json.dumps(self.out)
        self.assertNotIn('did:privy', blob)
        self.assertNotIn('team-fixture', blob)      # the service's internal handle is not the public name
        self.assertEqual(self.out['author'], '@fixture_author')

    def test_only_web_links_are_passed_as_sources_and_a_bare_url_is_not_a_title(self):
        urls = [s['url'] for s in self.out['sources']]
        self.assertEqual(urls, ['https://example.com/article-one', 'https://example.com/bare'])
        self.assertIsNone(self.out['sources'][1]['title'])
        self.assertNotIn('excerpt', self.out['sources'][0])   # third-party article text is not re-served

    def test_the_paper_basket_is_counted_and_only_listed_on_request(self):
        self.assertEqual(self.out['paper_holdings_count'], 2)
        self.assertIsNone(self.out['paper_holdings'])
        asked = shape.thesis_detail(fixture('thesis'), BASE, with_paper=True)
        self.assertEqual(len(asked['paper_holdings']), 2)

    def test_text_is_labelled_as_content(self):
        self.assertIn('not instructions', self.out['text_origin'])

    def test_a_thesis_missing_optional_parts_does_not_invent_them(self):
        d = fixture('thesis')
        for k in ('why_now', 'counter_case', 'venue_baskets', 'paper_legs', 'milestones', 'evidence', 'policy', 'cash_weight'):
            d['body'].pop(k)
        o = shape.thesis_detail(d, BASE)
        self.assertIsNone(o['why_now'])
        self.assertIsNone(o['cash_weight_pct'])
        self.assertEqual((o['other_baskets'], o['checkpoints'], o['sources'], o['paper_holdings_count']), ([], [], [], 0))
        self.assertEqual(o['upkeep'], {'mode': None, 'check_every_hours': None, 'rule': None})


class Performance(unittest.TestCase):
    def test_figures_are_the_apps_rounded(self):
        p = shape.performance(fixture('performance'), 90)
        self.assertEqual(p['kind'], 'hypothetical_price_history')
        self.assertEqual(p['return_pct'], 11.36)
        self.assertEqual(p['benchmark'], 'BTC')
        self.assertTrue(p['complete'])
        self.assertEqual(p['latest']['return_pct'], 8.65)
        self.assertNotIn('curve', p)

    def test_a_replay_without_a_return_states_none(self):
        raw = fixture('performance')
        raw['return_pct'] = None
        raw['complete'] = False
        raw['excluded'] = [{'instrument_id': 'perp:X'}]
        raw['latest'] = {'return_pct': None}
        p = shape.performance(raw, 30)
        self.assertIsNone(p['return_pct'])
        self.assertFalse(p['complete'])
        self.assertEqual(p['holdings_left_out'], 1)
        self.assertIsNone(p['latest'])

    def test_not_an_object_is_no_performance(self):
        self.assertIsNone(shape.performance(None, 90))
        self.assertIsNone(shape.performance([], 90))


class Cards(unittest.TestCase):
    def test_a_results_card(self):
        row = fixture('catalogue')['items'][0]
        c = shape.thesis_card(row, BASE)
        self.assertEqual(c['title'], 'Fixture card 1')
        self.assertEqual(c['holdings_count'], len(row['legs']))
        self.assertLessEqual(len(c['top_holdings']), 6)
        self.assertEqual(set(c['top_holdings'][0]), {'symbol', 'side', 'weight_pct', 'venue'})
        self.assertNotIn('did:privy', json.dumps(c))

    def test_a_featured_card_states_a_return_only_for_a_complete_replay(self):
        done, partial = fixture('features')['candidates']
        self.assertIsNotNone(shape.featured_card(done, BASE)['return_1m_pct'])
        c = shape.featured_card(partial, BASE)       # same figure in the payload, replay not complete
        self.assertIsNone(c['return_1m_pct'])
        self.assertIsNone(c['return_1m_from'])


class Markets(unittest.TestCase):
    def test_a_perp_row(self):
        rows = fixture('screener_hl')['rows']
        m = shape.perp_market('io:OAI', rows['io:OAI'], {'io:OAI': {'name': 'OpenAI', 'asset_class': 'preipo'}}, BASE)
        self.assertEqual((m['market_id'], m['symbol'], m['name'], m['venue']), ('perp:io:OAI', 'OAI', 'OpenAI', 'Entropy'))
        self.assertEqual(m['url'], BASE + '/m/io/io%3AOAI')
        self.assertEqual(shape.perp_market('BTC', rows['BTC'], {}, BASE)['url'], BASE + '/m/hl/BTC')

    def test_a_perp_row_with_holes_states_none(self):
        m = shape.perp_market('BTC', {'px': None, 'chg': 'x'}, {}, BASE)
        self.assertEqual((m['price'], m['change_24h_pct'], m['funding_apr_pct'], m['open_interest_usd'], m['volume_24h_usd']),
                         (None, None, None, None, None))

    def test_a_spot_token_carries_its_snapshot_figures_and_says_how_old_they_can_be(self):
        row = dict(fixture('screener_spot')['rows'][0])
        m = shape.spot_market(row, BASE)
        self.assertEqual((m['price'], m['change_24h_pct'], m['volume_24h_usd'], m['liquidity_usd'], m['market_cap_usd'], m['holders']),
                         (123.45, -1.5, 1000.0, 500.0, 2000.0, 10))
        self.assertIn('can be minutes to a few hours old', m['price_note'])
        # a figure the snapshot does not hold is None, never 0
        bare = shape.spot_market({'address': row['address'], 'chainId': 4663, 'sym': 'X', 'price': None, 'daily': 'n/a', 'vol': float('nan')}, BASE)
        self.assertEqual((bare['price'], bare['change_24h_pct'], bare['volume_24h_usd'], bare['liquidity_usd'], bare['holders']), (None, None, None, None, None))
        for raw in ('daily', 'vol', 'cap', 'fdv', 'liq', 'suspiciousPct'):
            self.assertNotIn(raw, m)                                              # the app's own field names do not leak through

    def test_the_figures_can_be_withdrawn_and_then_nothing_of_them_is_left(self):
        row = fixture('screener_spot')['rows'][1]
        m = shape.spot_market(row, BASE, figures=False)
        self.assertIsNone(m['price'])
        for field in ('change_24h_pct', 'volume_24h_usd', 'liquidity_usd', 'market_cap_usd', 'holders', 'daily', 'vol', 'cap', 'fdv', 'liq'):
            self.assertNotIn(field, m)
        self.assertNotIn('123.45', json.dumps(m))
        self.assertEqual(m['price_note'], 'Open the link for live price and liquidity.')
        self.assertEqual(m['venue'], 'Robinhood Chain')
        self.assertTrue(m['url'].startswith(BASE + '/m/rhc/0x'))

    def test_a_spot_row_without_an_address_is_dropped_not_guessed(self):
        self.assertIsNone(shape.spot_market({'sym': 'X', 'chainId': 4663}, BASE))
        self.assertIsNone(shape.spot_market({'sym': 'X', 'address': '0xabc', 'chainId': '4663'}, BASE))

    def test_search_ranking(self):
        s = shape.match_score
        self.assertGreater(s('nvda', 'NVDA', None), s('nvda', 'NVDAX', None))
        self.assertGreater(s('openai', 'OAI', 'OpenAI'), s('openai', 'X', 'OpenAI tracker'))
        self.assertEqual(s('zzz', 'NVDA', 'NVIDIA'), 0)
        self.assertEqual(s('', 'NVDA', None), 1)


if __name__ == '__main__':
    unittest.main()
