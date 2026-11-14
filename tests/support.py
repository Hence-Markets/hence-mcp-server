"""Shared test helpers: fixtures and a stand-in for the app."""
import copy
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
T1 = '11111111-1111-4111-8111-111111111111'
T2 = '22222222-2222-4222-8222-222222222222'


def fixture(name):
    with open(os.path.join(HERE, 'fixtures', name + '.json'), encoding='utf-8') as f:
        return json.load(f)


class StubUpstream:
    """Answers get_json from a table of path -> payload | Exception. Records every call, so a
    test can assert what was (and was not) read."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []

    def get_json(self, path, query=None, ttl=0):
        self.calls.append((path, dict(query or {})))
        if path not in self.routes:
            raise AssertionError('unexpected upstream read: %s' % path)
        answer = self.routes[path]
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return answer(dict(query or {}))
        return copy.deepcopy(answer)


def standard_routes():
    ws = '/api/thesis-workspace'
    return {
        ws + '/catalogue': fixture('catalogue'),
        ws + '/discovery/features': fixture('features'),
        ws + '/theses/' + T1: fixture('thesis'),
        ws + '/theses/' + T1 + '/performance': fixture('performance'),
        ws + '/theses/' + T1 + '/related': fixture('related'),
        '/api/screener/hl': fixture('screener_hl'),
        '/api/screener/spot': fixture('screener_spot'),
        # outcome markets: one two-sided market, one question with two answers, one that has settled
        '/api/hip4/events': fixture('hip4_events'),
        # Hence's Solana catalog: a stock with two issuers' tokens, one whose first token is halted, one halted everywhere, one not offered
        '/api/solana/assets': fixture('solana_assets'),
        '/api/config': {'solanaSpot': True, 'solanaResearch': True},
    }
