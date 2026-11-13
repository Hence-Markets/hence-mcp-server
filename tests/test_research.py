"""Hence's own research, started and followed for the person: start_thesis_research,
get_thesis_research, and `hence research`.

What these pin:
  * the tool that starts research spends Hence's money, so it is offered only behind its own
    switch, behind its own permission, and marked so a host asks the person first;
  * whether a run started, whether it finished and whether there is a thesis are passed on only
    as the app said them: an answer that cannot be read is an error, never "started", never
    "finished", never a thesis id;
  * following a run waits for Hence to say it finished, and a failed check does not end the wait.
"""
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from gateway import appauth, protocol, shape, tools
from hence_cli import main
from hence_cli.store import Store

from .support import StubUpstream, standard_routes
from .test_account import GRANT, ON, TOKEN, modern

BASE = '/api/agents/v1/research'
RUN = '99999999-9999-4999-8999-999999999999'
THESIS = '11111111-1111-4111-8111-111111111111'
ALL = ['account:read', 'theses:read', 'theses:research']


def run(**over):
    r = {'run_id': RUN, 'status': 'queued', 'finished': False, 'idea': 'AI capex outruns demand', 'now': None, 'started_at': '2026-11-14T00:00:00+00:00',
         'finished_at': None, 'thesis_id': None, 'url': None, 'progress_url': 'https://app.hence.markets/#/thesis/lab/run/' + RUN}
    r.update(over)
    return r


DONE = run(status='complete', finished=True, thesis_id=THESIS, url='https://app.hence.markets/t/' + THESIS, finished_at='2026-11-14T00:30:00+00:00',
           outcome='The research finished and the thesis is in the person\'s Hence library.')
STARTED = {'ok': True, 'started': True, 'problems': [], 'run': run()}


class App:
    def __init__(self, answers=None, scopes=ALL):
        self.answers, self.scopes, self.sent, self.read = dict(answers or {}), list(scopes), [], []

    def connection(self, token):
        return {'grant': GRANT, 'scopes': self.scopes, 'client_name': 'Example Agent'}

    def _answer(self, path):
        answer = self.answers[path]
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, BaseException):
            raise answer
        return json.loads(json.dumps(answer))

    def post(self, grant, path, payload):
        self.sent.append((path, json.loads(json.dumps(payload))))
        return self._answer(path)

    def get(self, grant, path, query=None):
        self.read.append(path)
        return self._answer(path)

    def forget(self, token):
        pass


class Case(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, dict(ON, HENCE_MCP_RESEARCH='1'))
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


class Starting(Case):
    def test_a_start_sends_the_persons_steering_and_says_what_it_cost(self):
        app = App({BASE: STARTED})
        ask = {'belief': 'AI capex outruns demand', 'guidance': 'No leverage.', 'must_include': ['perp:xyz:NVDA'], 'exclude': ['perp:xyz:TSLA'], 'basket': 'perps'}
        got = self.value('start_thesis_research', ask, app)
        self.assertEqual(app.sent, [(BASE, ask)])
        self.assertEqual((got['started'], got['run']['run_id'], got['run']['status']), (True, RUN, 'queued'))
        for said in ('20 to 40 minutes', 'counted toward the person\'s daily limit', 'reaches this same run'):
            self.assertIn(said, got['next'])

    def test_it_is_not_offered_or_callable_without_its_switch(self):
        app = App({BASE: STARTED})
        with mock.patch.dict(os.environ, {'HENCE_MCP_RESEARCH': '0'}):
            out = self.call('start_thesis_research', {'belief': 'idea'}, app)
            self.assertIn('Unknown tool', out['error']['message'])
            self.assertIsNotNone(tools.find('get_thesis_research'))                # following stays available
        self.assertEqual(app.sent, [])

    def test_it_needs_its_own_permission(self):
        app = App({BASE: STARTED}, scopes=['account:read', 'theses:read', 'theses:write'])
        self.call('start_thesis_research', {'belief': 'idea'}, app)
        self.assertEqual(self.status, 403)
        self.assertIn('scope="theses:research"', self.headers['WWW-Authenticate'])
        self.assertEqual(app.sent, [])

    def test_problems_and_limits_reach_the_agent_and_nothing_is_called_started(self):
        bad = {'ok': False, 'started': False, 'problems': [{'field': 'exclude[0]', 'problem': 'Hence does not list a perpetual "NOPE".'}]}
        got = self.value('start_thesis_research', {'belief': 'idea', 'exclude': ['perp:NOPE']}, App({BASE: bad}))
        self.assertEqual((got['started'], got['problems'][0]['field']), (False, 'exclude[0]'))
        self.assertIn('Nothing was started', got['next'])
        said = 'You have created 5 theses in the last 24 hours. Manual creation remains available.'
        self.assertEqual(self.refusal('start_thesis_research', {'belief': 'idea'}, App({BASE: appauth.Refused(409, 'limit_reached', said)})), said)

    def test_an_unreadable_answer_is_never_started(self):
        for broken in ({}, {'ok': True, 'started': True}, {'ok': True, 'started': True, 'run': {'run_id': 'x'}}, {'ok': True, 'started': False, 'run': run()},
                       {'ok': False, 'started': True, 'problems': [{'field': 'x', 'problem': 'y'}]}, {'ok': False, 'started': False, 'problems': []},
                       {'ok': True, 'started': True, 'run': run(status='queued', finished=True)}, 'text', 7):
            text = self.refusal('start_thesis_research', {'belief': 'idea'}, App({BASE: broken}))
            self.assertIn('could not be read', text, repr(broken)[:60])
            self.assertIn('the same request reaches the same run', text)
        self.assertIn('Unknown argument', self.refusal('start_thesis_research', {'belief': 'idea', 'visibility': 'private'}, App({BASE: STARTED})))


class Following(Case):
    def test_a_running_run_says_come_back_and_a_finished_one_names_the_thesis(self):
        got = self.value('get_thesis_research', {'run_id': RUN.upper()}, App({BASE + '/' + RUN: run(status='running', now='Screening 2,300 markets')}))
        self.assertEqual((got['status'], got['finished'], got['now'], got['thesis_id']), ('running', False, 'Screening 2,300 markets', None))
        self.assertIn('check again in about ten minutes', got['next'])
        done = self.value('get_thesis_research', {'run_id': RUN}, App({BASE + '/' + RUN: DONE}))
        self.assertEqual((done['finished'], done['thesis_id'], done['url']), (True, THESIS, 'https://app.hence.markets/t/' + THESIS))
        self.assertIn('It is Hence research, not yours', done['next'])

    def test_an_ending_without_a_thesis_says_so(self):
        failed = run(status='abstained', finished=True, outcome='Hence researched the idea and chose not to build a basket for it. No thesis was created.')
        got = self.value('get_thesis_research', {'run_id': RUN}, App({BASE + '/' + RUN: failed}))
        self.assertEqual((got['thesis_id'], got['url']), (None, None))
        self.assertIn('No thesis was created', got['next'])

    def test_finished_and_status_must_agree(self):
        for broken in (run(status='running', finished=True), run(status='complete', finished=False), run(status='exploded'), run(run_id='x'), {}, None):
            self.assertIsNone(shape.research_run(broken), repr(broken)[:60])
            self.assertIn('could not be read', self.refusal('get_thesis_research', {'run_id': RUN}, App({BASE + '/' + RUN: broken})))

    def test_the_list_and_a_bad_id(self):
        listed = {'in_progress': [{'run_id': RUN, 'idea': 'x', 'stage': 'Choosing the holdings'}], 'recently_finished': []}
        self.assertEqual(self.value('get_thesis_research', {}, App({BASE: listed})), listed)
        app = App()
        self.assertIn('not a research run id', self.refusal('get_thesis_research', {'run_id': '../portfolio'}, app))
        self.assertEqual(app.read, [])
        gone = App({BASE + '/' + RUN: appauth.Refused(404, 'not_found', 'There is no research run with that id on this account.')})
        self.assertEqual(self.refusal('get_thesis_research', {'run_id': RUN}, gone), 'There is no research run with that id on this account.')


class Command(unittest.TestCase):
    """`hence research`, with the server's own tool functions behind it."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = Store(os.path.join(self.dir.name, 'credentials.json'))
        self.store.update('https://mcp.hence.markets/mcp', access_token='hat_x', expires_at=None)
        self.app, self.slept = None, []

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
        if not headers.get('Authorization'):
            return 401, json.dumps({'error': {'message': 'Sign in.'}}).encode(), {'www-authenticate': 'Bearer scope="theses:read"'}
        try:
            value = (tools.start_thesis_research if name == 'start_thesis_research' else tools.get_thesis_research)(args, None, Account(self.app))
            result = {'content': [{'type': 'text', 'text': json.dumps(value)}], 'structuredContent': value, 'isError': False}
        except (appauth.Unavailable, appauth.Refused) as e:
            result = {'content': [{'type': 'text', 'text': str(e)}], 'isError': True}
        return 200, json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': result}).encode(), {}

    def cli(self, *argv, confirm=None):
        out, err = io.StringIO(), io.StringIO()
        now = [0.0]

        def sleep(seconds):
            self.slept.append(seconds)
            now[0] += seconds
        code = main.run(list(argv), out=out, err=err, store=self.store, sender=self.sender, confirm=confirm, sleep=sleep, clock=lambda: now[0])
        return code, out.getvalue(), err.getvalue()

    def test_start_asks_first_and_no_starts_nothing(self):
        self.app = App({BASE: STARTED})
        code, out, err = self.cli('research', 'start', 'AI capex outruns demand', confirm=lambda: False)
        self.assertEqual((code, out, self.app.sent), (1, '', []))
        self.assertIn('Not started', err)
        out2, err2 = io.StringIO(), io.StringIO()
        code = main.run(['research', 'start', 'idea'], out=out2, err=err2, store=self.store, sender=self.sender, stdin=io.StringIO(''))
        self.assertEqual((code, self.app.sent), (1, []))                                # no terminal to ask in, no --yes: nothing spent

    def test_start_sends_the_steering_and_prints_the_run(self):
        self.app = App({BASE: STARTED})
        code, out, _ = self.cli('research', 'start', 'AI', 'capex', 'outruns', 'demand', '--guidance', 'No leverage.', '--must-include', 'perp:xyz:NVDA',
                                '--exclude', 'perp:xyz:TSLA', '--basket', 'perps', '--yes')
        self.assertEqual(code, 0)
        self.assertEqual(self.app.sent, [(BASE, {'belief': 'AI capex outruns demand', 'guidance': 'No leverage.', 'must_include': ['perp:xyz:NVDA'],
                                               'exclude': ['perp:xyz:TSLA'], 'basket': 'perps'})])
        for said in ('AI capex outruns demand  [queued]', 'thesis: not ready yet', 'run: ' + RUN, '20 to 40 minutes'):
            self.assertIn(said, out)

    def test_wait_follows_until_hence_says_it_finished_and_survives_a_failed_check(self):
        self.app = App({BASE: STARTED, BASE + '/' + RUN: [run(status='running', now='Reading the idea'), appauth.Unavailable('Hence answered 503'),
                                                          run(status='running', now='Choosing the holdings'), DONE]})
        code, out, err = self.cli('research', 'start', 'idea', '--yes', '--wait')
        self.assertEqual(code, 0)
        self.assertEqual(self.slept, [60, 60, 60])
        for said in ('Reading the idea', 'could not check just now', 'Still watching', 'Choosing the holdings'):
            self.assertIn(said, err)
        self.assertIn('thesis: https://app.hence.markets/t/' + THESIS, out)
        self.assertIn('[complete]', out)

    def test_a_run_that_ends_without_a_thesis_is_exit_1_and_says_why(self):
        ended = run(status='failed', finished=True, outcome='Research was interrupted. Your input and saved progress are safe; retry when ready.')
        self.app = App({BASE: STARTED, BASE + '/' + RUN: ended})
        code, out, _ = self.cli('research', 'start', 'idea', '--yes', '--wait')
        self.assertEqual(code, 1)
        self.assertIn('Research was interrupted', out)
        self.assertIn('thesis: none', out)

    def test_watching_gives_up_after_an_hour_and_says_the_run_goes_on(self):
        self.app = App({BASE + '/' + RUN: run(status='running')})
        code, out, err = self.cli('research', 'status', RUN, '--wait')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('It carries on without this terminal', err)
        self.assertGreaterEqual(sum(self.slept), 3600)

    def test_status_and_list(self):
        self.app = App({BASE + '/' + RUN: run(status='running', now='Pricing and sizing'),
                        BASE: {'in_progress': [{'run_id': RUN, 'idea': 'AI capex', 'stage': 'Choosing the holdings', 'url': 'https://app.hence.markets/#/x'}],
                               'recently_finished': [{'run_id': RUN, 'idea': 'Done one', 'result': 'a thesis is ready', 'at': 'b', 'url': 'https://app.hence.markets/#/y'}]}})
        code, out, _ = self.cli('research', 'status', 'https://app.hence.markets/#/thesis/lab/run/' + RUN)
        self.assertEqual(code, 0)
        self.assertIn('now: Pricing and sizing', out)
        code, out, _ = self.cli('research', 'list')
        for said in ('In progress', 'Choosing the holdings', 'Recently finished', 'a thesis is ready'):
            self.assertIn(said, out)
        self.assertEqual(self.cli('research', 'status', 'nope')[0], 1)


if __name__ == '__main__':
    unittest.main()
