"""A thesis the agent writes itself: get_thesis_guide, check_thesis, save_thesis_draft.

What these pin:
  * saving is a private draft in the person's library, never a thesis: this server cannot
    create, publish or trade one, and it says so in the answer;
  * Hence's verdict is passed on as Hence gave it: problems are never dropped, "ok" is never
    assumed, and an answer that cannot be read is an error, not a pass;
  * the guide an agent reads from the tool and the one in the skill are the same text.
"""
import json
import os
import unittest
from unittest import mock

from gateway import appauth, protocol, shape, tools

from .support import StubUpstream, standard_routes
from .test_account import GRANT, ON, SECRET, TOKEN, modern

BASE = '/api/agents/v1/theses'
HERE = os.path.dirname(os.path.abspath(__file__))
THESIS = {'format': 'hence-thesis/1', 'title': 'Overfunded buildout', 'summary': 's', 'mechanism': 'm', 'why_now': 'w', 'counter_case': 'c',
          'invalidation': 'i', 'horizon_days': 180, 'cash_weight_pct': 20,
          'holdings': [{'market_id': 'perp:xyz:NVDA', 'side': 'short', 'weight_pct': 80, 'role': 'core', 'reason': 'r'}]}
PREVIEW = {'title': 'Overfunded buildout', 'topic': 'Other', 'horizon_days': 180, 'cash_weight_pct': 20.0, 'basket': 'Perps',
           'holdings': [{'market_id': 'perp:xyz:NVDA', 'symbol': 'xyz:NVDA', 'name': 'NVDA', 'venue': 'Hyperliquid', 'side': 'short', 'weight_pct': 80.0, 'role': 'core'}]}
GOOD = {'ok': True, 'saved': False, 'problems': [], 'warnings': ['No sources are cited.'], 'thesis': PREVIEW}
BAD = {'ok': False, 'saved': False, 'problems': [{'field': 'title', 'problem': 'Required. Write the title.'},
                                               {'field': 'holdings[0].market_id', 'problem': 'Hence does not list a perpetual "NOPE".'}], 'warnings': []}
SAVED = dict(GOOD, saved=True, saved_as='draft', draft_id='d-1', already_saved=False, url='https://app.hence.markets/#/thesis/create?draft=d-1')


class App:
    def __init__(self, answers=None, scopes=('account:read', 'theses:read', 'theses:write')):
        self.answers, self.scopes, self.sent = dict(answers or {}), list(scopes), []

    def connection(self, token):
        return {'grant': GRANT, 'scopes': self.scopes, 'client_name': 'Example Agent'}

    def post(self, grant, path, payload):
        self.sent.append((grant, path, json.loads(json.dumps(payload))))
        answer = self.answers[path]
        if isinstance(answer, BaseException):
            raise answer
        return json.loads(json.dumps(answer))

    def get(self, grant, path, query=None):
        raise AssertionError('a write tool read %s' % path)

    def forget(self, token):
        pass


class Case(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, ON)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.up = StubUpstream(standard_routes())

    def call(self, name, arguments=None, app=None, signed_in=True):
        headers = {'authorization': 'Bearer ' + TOKEN} if signed_in else None
        status, out, extra = protocol.handle_full(*modern('tools/call', {'name': name, 'arguments': arguments or {}}, headers, name), self.up, auth=app)
        self.status, self.headers = status, extra or {}
        return out.get('result') if isinstance(out, dict) else None

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


class Guide(Case):
    def test_the_guide_is_public_and_is_the_skills_own_text(self):
        got = self.value('get_thesis_guide', {}, None, signed_in=False)
        with open(os.path.join(HERE, '..', 'skills', 'hence', 'write-a-thesis.md'), encoding='utf-8') as f:
            skill = f.read()
        self.assertEqual(got['guide'], skill)                          # one method, whichever way it arrives
        self.assertEqual(got['format'], 'hence-thesis/1')
        self.assertIn('not Hence research', got['note'])

    def test_the_guide_names_only_tools_fields_and_rules_that_exist(self):
        import re
        with open(os.path.join(HERE, '..', 'data', 'write-a-thesis.md'), encoding='utf-8') as f:
            text = f.read()
        known = {t['name'] for t in tools.TOOLS + tools.ACCOUNT_TOOLS}
        for name in set(re.findall(r'`((?:search|get|check|save|list)_[a-z_]+)`', text)):
            self.assertIn(name, known, name)
        example = json.loads(re.search(r'```json\n(.*?)\n```', text, re.S).group(1))
        props = tools.THESIS['properties']
        self.assertLessEqual(set(example), set(props))
        self.assertLessEqual(set(tools.THESIS['required']), set(example))
        self.assertLessEqual(set(example['holdings'][0]), set(props['holdings']['items']['properties']))
        self.assertLessEqual(set(example['sources'][0]), set(props['sources']['items']['properties']))
        self.assertLessEqual(set(example['checkpoints'][0]), set(props['checkpoints']['items']['properties']))
        self.assertIn(example['checkpoints'][0]['direction'], props['checkpoints']['items']['properties']['direction']['enum'])
        for said in ('Do not save a thesis the person has not seen', 'Do not present it as Hence', 'One venue per basket', 'Knowledge, unverified:'):
            self.assertIn(said, text)

    def test_the_schema_carries_the_services_limits(self):
        p = tools.THESIS['properties']
        self.assertEqual([p[k]['maxLength'] for k in ('title', 'summary', 'mechanism', 'why_now', 'counter_case', 'invalidation')], [180, 1000, 4000, 2000, 2000, 2000])
        self.assertEqual((p['holdings']['maxItems'], p['checkpoints']['maxItems'], p['sources']['maxItems'], p['horizon_days']['maximum']), (20, 5, 30, 10950))
        self.assertEqual(p['holdings']['items']['properties']['role']['enum'], ['core', 'second_order', 'hedge'])


class Checking(Case):
    def test_check_sends_the_thesis_and_saves_nothing(self):
        app = App({BASE + '/check': GOOD})
        got = self.value('check_thesis', {'thesis': THESIS}, app)
        self.assertEqual(app.sent, [(GRANT, BASE + '/check', THESIS)])
        self.assertEqual((got['ok'], got['saved'], got['problems'], got['warnings'], got['thesis']), (True, False, [], ['No sources are cited.'], PREVIEW))
        self.assertIn('Nothing is saved yet', got['next'])
        self.assertIn('Show the person', got['next'])

    def test_every_problem_reaches_the_agent(self):
        got = self.value('check_thesis', {'thesis': THESIS}, App({BASE + '/check': BAD}))
        self.assertEqual((got['ok'], got['saved'], [p['field'] for p in got['problems']]), (False, False, ['title', 'holdings[0].market_id']))
        self.assertIn('Nothing was saved', got['next'])
        self.assertNotIn('thesis', got)

    def test_a_verdict_that_cannot_be_read_is_not_a_pass(self):
        for broken in ({}, {'ok': True}, {'ok': 'yes', 'saved': False, 'problems': [], 'warnings': []}, {'ok': True, 'saved': False, 'problems': None, 'warnings': []},
                       {'ok': False, 'saved': False, 'problems': [], 'warnings': []},                       # "not ok" with no reason
                       {'ok': True, 'saved': False, 'problems': [{'field': 'x', 'problem': 'y'}], 'warnings': []},   # ok AND problems
                       {'ok': False, 'saved': True, 'problems': [{'field': 'x', 'problem': 'y'}], 'warnings': []},  # saved but not ok
                       [], 'text'):
            self.assertIn('could not be read', self.refusal('check_thesis', {'thesis': THESIS}, App({BASE + '/check': broken})), repr(broken)[:70])

    def test_a_thesis_must_be_an_object_and_nothing_else_is_sent(self):
        app = App({BASE + '/check': GOOD})
        for bad in ({}, {'thesis': 'text'}, {'thesis': [THESIS]}, {'thesis': THESIS, 'publish': True}, {'thesis': THESIS, 'visibility': 'public'}):
            self.refusal('check_thesis', bad, app)
            self.refusal('save_thesis_draft', bad, app)
        self.assertEqual(app.sent, [])

    def test_hence_not_answering_is_try_again(self):
        self.assertIn('try again shortly', self.refusal('check_thesis', {'thesis': THESIS}, App({BASE + '/check': appauth.Unavailable('Hence answered 503')})))
        self.assertEqual(self.refusal('check_thesis', {'thesis': THESIS}, App({BASE + '/check': appauth.Refused(413, 'invalid_request', 'A thesis is at most 64 KB.')})),
                         'A thesis is at most 64 KB.')


class Saving(Case):
    def test_a_save_is_a_draft_and_the_answer_says_what_it_is_not(self):
        app = App({BASE + '/drafts': SAVED})
        got = self.value('save_thesis_draft', {'thesis': THESIS}, app)
        self.assertEqual(app.sent, [(GRANT, BASE + '/drafts', THESIS)])
        self.assertEqual((got['saved'], got['saved_as'], got['draft_id'], got['url'], got['already_saved']),
                         (True, 'draft', 'd-1', 'https://app.hence.markets/#/thesis/create?draft=d-1', False))
        for said in ('private draft', 'they review it in Hence and save it there', 'Hence has not researched it', 'Nothing was traded'):
            self.assertIn(said, got['next'])

    def test_a_thesis_with_problems_is_not_saved_and_says_so(self):
        got = self.value('save_thesis_draft', {'thesis': THESIS}, App({BASE + '/drafts': BAD}))
        self.assertEqual((got['ok'], got['saved']), (False, False))
        self.assertNotIn('url', got)
        self.assertIn('Nothing was saved', got['next'])

    def test_a_save_with_nowhere_to_send_the_person_is_not_reported_as_one(self):
        for broken in (dict(SAVED, url=''), dict(SAVED, url=None), dict(SAVED, draft_id='')):
            self.assertIn('could not be read', self.refusal('save_thesis_draft', {'thesis': THESIS}, App({BASE + '/drafts': broken})))

    def test_writing_needs_its_own_permission(self):
        app = App({BASE + '/check': GOOD, BASE + '/drafts': SAVED}, scopes=['account:read', 'theses:read'])
        for name in ('check_thesis', 'save_thesis_draft'):
            self.call(name, {'thesis': THESIS}, app)
            self.assertEqual(self.status, 403, name)
            self.assertIn('scope="theses:write"', self.headers['WWW-Authenticate'])
            self.call(name, {'thesis': THESIS}, app, signed_in=False)
            self.assertEqual(self.status, 401, name)
        self.assertEqual(app.sent, [])

    def test_this_server_can_send_a_thesis_to_two_places_only(self):
        sent = []

        def send(url, method, headers, data, timeout):
            sent.append((url, method, json.loads(data), headers))
            return 200, json.dumps(GOOD).encode(), {}
        auth = appauth.AppAuth(base='http://app.test', send=send)
        auth.post(GRANT, BASE + '/check', THESIS)
        url, method, body, headers = sent[0]
        self.assertEqual((url, method, body), ('http://app.test/api/agents/v1/theses/check', 'POST', THESIS))
        raw = json.dumps(THESIS, ensure_ascii=False, separators=(',', ':')).encode()
        expected = appauth.sign(SECRET, 'POST', BASE + '/check', GRANT, raw, headers['X-Hence-Agent-Time'], headers['X-Hence-Agent-Nonce'])
        self.assertEqual(headers['X-Hence-Agent-Signature'], expected['X-Hence-Agent-Signature'])          # the body is signed
        self.assertNotIn('Authorization', headers)
        for path in ('/api/agents/v1/theses', BASE + '/x/version', '/api/thesis-workspace/theses', '/api/exchange', '/api/me/theses/executions',
                     BASE + '/drafts?publish=1', '/api/agents/v1/portfolio'):
            with self.assertRaises(ValueError):
                auth.post(GRANT, path, THESIS)
        self.assertEqual(len(sent), 1)


class Verdict(unittest.TestCase):
    def test_what_to_do_next_matches_what_happened(self):
        self.assertIn('Nothing is saved yet', shape.thesis_check(GOOD)['next'])
        self.assertIn('Nothing was saved', shape.thesis_check(BAD)['next'])
        self.assertIn('Saved as a private draft', shape.thesis_check(SAVED)['next'])
        self.assertIs(shape.thesis_check(dict(SAVED, already_saved=True))['already_saved'], True)
        self.assertIsNone(shape.thesis_check(None))


if __name__ == '__main__':
    unittest.main()
