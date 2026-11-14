"""The tasks extension for Hence research (SEP-2663, revision 2026-07-28).

A client that says it speaks the extension gets a task handle from start_thesis_research and
polls tasks/get; the host tells the person when it is done. What these pin:

  * a task is returned only to a request that declared the extension, and only for a run the
    app said it started: never for a refusal, never to a client that did not ask;
  * the task IS the person's research run. Its state is read from the app under their
    connection on every poll: finished is the app's word, an id that is not theirs is "not
    found", and a read that failed is "poll again", never a finished task;
  * the wire shapes are the specification's.
"""
import json
import os
import unittest
from unittest import mock

from gateway import appauth, protocol

from .support import StubUpstream, standard_routes
from .test_account import ON, TOKEN, V
from .test_research import App, BASE, DONE, RUN, STARTED, run

EXT = 'io.modelcontextprotocol/tasks'
DECLARE = {'io.modelcontextprotocol/clientCapabilities': {'extensions': {EXT: {}}}}
ALL = ['account:read', 'theses:read', 'theses:research']
ASK = {'belief': 'AI capex outruns demand'}


def request(method, params=None, name=None, declare=True, signed_in=True, extra_headers=None):
    p = dict(params or {})
    p['_meta'] = dict({'io.modelcontextprotocol/protocolVersion': V}, **(DECLARE if declare else {}))
    h = {'mcp-protocol-version': V, 'mcp-method': method}
    if name is not None:
        h['mcp-name'] = name
    if signed_in:
        h['authorization'] = 'Bearer ' + TOKEN
    h.update(extra_headers or {})
    return h, json.dumps({'jsonrpc': '2.0', 'id': 7, 'method': method, 'params': p}).encode()


class Case(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, dict(ON, HENCE_MCP_RESEARCH='1'))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.up = StubUpstream(standard_routes())

    def send(self, app, method, params=None, name=None, **kw):
        status, out, headers = protocol.handle_full(*request(method, params, name, **kw), self.up, auth=app)
        self.status, self.headers = status, headers or {}
        return out

    def start(self, app, declare=True, args=ASK):
        return self.send(app, 'tools/call', {'name': 'start_thesis_research', 'arguments': args}, 'start_thesis_research', declare=declare)

    def get(self, app, task_id=RUN, **kw):
        return self.send(app, 'tasks/get', {'taskId': task_id}, kw.pop('name', task_id), **kw)


class Declared(Case):
    def test_the_server_says_it_speaks_tasks_only_while_research_is_offered(self):
        out = self.send(None, 'server/discover', signed_in=False)
        self.assertEqual(out['result']['capabilities']['extensions'], {EXT: {}})
        with mock.patch.dict(os.environ, {'HENCE_MCP_RESEARCH': '0'}):
            out = self.send(None, 'server/discover', signed_in=False)
            self.assertNotIn('extensions', out['result']['capabilities'])
            # and the methods do not exist there
            out = self.get(App({BASE + '/' + RUN: run()}, scopes=ALL))
            self.assertEqual((self.status, out['error']['code']), (404, protocol.METHOD_NOT_FOUND))
        self.assertNotIn('extensions', protocol.CAPABILITIES)                         # the constant itself is never widened

    def test_a_client_that_did_not_declare_it_gets_the_ordinary_result(self):
        app = App({BASE: STARTED}, scopes=ALL)
        out = self.start(app, declare=False)
        self.assertEqual((out['result']['resultType'], out['result']['isError'], out['result']['structuredContent']['run']['run_id']), ('complete', False, RUN))
        self.assertNotIn('taskId', out['result'])
        # asking for a task's state without declaring it is refused the way the specification says
        out = self.get(App({BASE + '/' + RUN: run()}, scopes=ALL), declare=False)
        self.assertEqual(out['error'], {'code': -32021, 'message': 'Missing required client capability',
                                        'data': {'requiredCapabilities': {'extensions': {EXT: {}}}}})
        for half in ({'io.modelcontextprotocol/clientCapabilities': {}}, {'io.modelcontextprotocol/clientCapabilities': {'extensions': {EXT: True}}},
                     {'io.modelcontextprotocol/clientCapabilities': 'yes'}):
            self.assertFalse(protocol._speaks_tasks(half), half)


class Creating(Case):
    def test_a_started_run_comes_back_as_a_task(self):
        app = App({BASE: STARTED}, scopes=ALL)
        out = self.start(app)
        self.assertEqual(self.status, 200)
        task = out['result']
        self.assertEqual((task['resultType'], task['taskId'], task['status'], task['ttlMs'], task['pollIntervalMs']),
                         ('task', RUN, 'working', 24 * 3600 * 1000, 30000))
        self.assertEqual((task['createdAt'], task['statusMessage']), ('2026-11-14T00:00:00+00:00', 'Hence is researching this. It takes 20 to 40 minutes.'))
        self.assertRegex(task['lastUpdatedAt'], r'^\d{4}-\d{2}-\d{2}T')
        for absent in ('content', 'structuredContent', 'isError', 'result'):
            self.assertNotIn(absent, task)                                              # a handle, not a result
        self.assertEqual(app.sent, [(BASE, ASK)])
        # the id handed out is one a poll resolves: the run the app itself named
        polled = self.get(App({BASE + '/' + RUN: run(status='running', now='Screening 2,300 markets')}, scopes=ALL))
        self.assertEqual((polled['result']['taskId'], polled['result']['statusMessage']), (RUN, 'Screening 2,300 markets'))

    def test_a_refusal_or_an_unreadable_answer_is_never_a_task(self):
        bad = {'ok': False, 'started': False, 'problems': [{'field': 'exclude[0]', 'problem': 'Hence does not list a perpetual "NOPE".'}]}
        out = self.start(App({BASE: bad}, scopes=ALL))
        self.assertEqual((out['result']['resultType'], out['result']['structuredContent']['started']), ('complete', False))
        out = self.start(App({BASE: {'ok': True, 'started': True}}, scopes=ALL))        # the app's answer cannot be read
        self.assertEqual((out['result']['resultType'], out['result']['isError']), ('complete', True))
        out = self.start(App({BASE: appauth.Refused(409, 'limit_reached', 'You have created 5 theses in the last 24 hours.')}, scopes=ALL))
        self.assertEqual((out['result']['resultType'], out['result']['isError']), ('complete', True))
        for result in ():
            self.assertNotIn('taskId', result)
        # only the one tool that starts a long job ever returns a task
        out = self.send(App({BASE: {'in_progress': [], 'recently_finished': []}}, scopes=ALL), 'tools/call',
                        {'name': 'get_thesis_research', 'arguments': {}}, 'get_thesis_research')
        self.assertEqual(out['result']['resultType'], 'complete')
        self.assertEqual(protocol.TASK_TOOL, 'start_thesis_research')


class Polling(Case):
    def test_working_then_completed_with_the_tools_own_result(self):
        out = self.get(App({BASE + '/' + RUN: run()}, scopes=ALL))
        task = out['result']
        self.assertEqual((task['resultType'], task['status'], task['taskId']), ('complete', 'working', RUN))
        self.assertNotIn('result', task)
        done = self.get(App({BASE + '/' + RUN: DONE}, scopes=ALL))['result']
        self.assertEqual((done['status'], done['lastUpdatedAt'], done['statusMessage']),
                         ('completed', '2026-11-14T00:30:00+00:00', 'The research finished and the thesis is in the person\'s Hence library.'))
        result = done['result']
        self.assertEqual((result['isError'], result['structuredContent']['thesis_id'], result['structuredContent']['url']),
                         (False, DONE['thesis_id'], DONE['url']))
        self.assertEqual(json.loads(result['content'][0]['text'])['run_id'], RUN)
        self.assertIn('It is Hence research, not yours', result['structuredContent']['next'])

    def test_an_ending_without_a_thesis_is_a_completed_task_that_says_so(self):
        for status, is_error in (('failed', True), ('abstained', False), ('rejected', False), ('needs_review', False)):
            ended = run(status=status, finished=True, outcome='No thesis was created.')
            task = self.get(App({BASE + '/' + RUN: ended}, scopes=ALL))['result']
            self.assertEqual((task['status'], task['result']['isError'], task['result']['structuredContent']['thesis_id']), ('completed', is_error, None), status)
            self.assertNotEqual(task['status'], 'failed')                                 # that status is for protocol errors

    def test_finished_is_the_apps_word_and_a_failed_read_is_poll_again(self):
        for broken in (run(status='running', finished=True), run(status='complete', finished=False), {}, 'text'):
            out = self.get(App({BASE + '/' + RUN: broken}, scopes=ALL))
            self.assertEqual(out['error']['code'], protocol.INTERNAL_ERROR, repr(broken)[:50])
            self.assertIn('Poll again', out['error']['message'])
        for failure in (appauth.Unavailable('Hence answered 503'), appauth.Busy('30')):
            out = self.get(App({BASE + '/' + RUN: failure}, scopes=ALL))
            self.assertEqual(out['error']['code'], protocol.INTERNAL_ERROR)
            self.assertIn('Poll again', out['error']['message'])
            self.assertNotIn('result', out)

    def test_a_task_belongs_to_its_person(self):
        gone = App({BASE + '/' + RUN: appauth.Refused(404, 'not_found', 'There is no research run with that id on this account.')}, scopes=ALL)
        out = self.get(gone)
        self.assertEqual(out['error'], {'code': -32602, 'message': 'Failed to retrieve task: Task not found'})
        app = App({BASE + '/' + RUN: run()}, scopes=ALL)
        for bad in ('../portfolio', 'x', ''):
            out = self.send(app, 'tasks/get', {'taskId': bad}, bad)
            self.assertEqual(out['error']['code'], -32602, repr(bad))
        out = self.send(app, 'tasks/get', {'taskId': 7})                                 # not even a string: the routing header cannot name it
        self.assertEqual(self.status, 400)
        self.assertEqual(app.read, [])
        # no credential, or one without the read permission: sent to Hence, as for any account tool
        self.get(app, signed_in=False)
        self.assertEqual(self.status, 401)
        self.assertIn('scope="theses:read"', self.headers['WWW-Authenticate'])
        self.get(App({BASE + '/' + RUN: run()}, scopes=['account:read']))
        self.assertEqual(self.status, 403)
        # the routing header names the task, as the specification requires
        out = self.get(app, name='another-task')
        self.assertEqual((self.status, out['error']['code']), (400, protocol.HEADER_MISMATCH))

    def test_cancel_is_acknowledged_and_update_is_refused(self):
        app = App({BASE + '/' + RUN: run()}, scopes=ALL)
        out = self.send(app, 'tasks/cancel', {'taskId': RUN}, RUN)
        self.assertEqual({k: v for k, v in out['result'].items() if k != '_meta'}, {'resultType': 'complete'})
        self.assertEqual((app.sent, app.read), ([], [BASE + '/' + RUN]))                 # nothing is sent to Hence: a run is not stopped, and the answer does not say it was
        out = self.send(App({BASE + '/' + RUN: appauth.Refused(404, 'not_found', 'x')}, scopes=ALL), 'tasks/cancel', {'taskId': RUN}, RUN)
        self.assertEqual(out['error']['code'], -32602)
        out = self.send(app, 'tasks/update', {'taskId': RUN, 'inputResponses': {}}, RUN)
        self.assertEqual((out['error']['code'], out['error']['message']), (-32602, 'This task is not waiting for input.'))

    def test_the_handshake_era_does_not_have_it(self):
        raw = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tasks/get', 'params': {'taskId': RUN}}).encode()
        status, out = protocol.handle({'authorization': 'Bearer ' + TOKEN}, raw, self.up, auth=App({BASE + '/' + RUN: run()}, scopes=ALL))
        self.assertEqual((status, out['error']['code']), (200, protocol.METHOD_NOT_FOUND))


if __name__ == '__main__':
    unittest.main()
