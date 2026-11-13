"""The MCP wire contract, both eras, exercised without a socket.

Modern = 2026-07-28 (stateless, version in params._meta, mirrored headers).
Legacy = 2025-03-26..2025-11-25 (initialize handshake).
"""
import base64
import json
import os
import unittest
from unittest import mock

from gateway import protocol, tools

from .support import T1, StubUpstream, standard_routes

V = '2026-07-28'


def modern(method, params=None, msg_id=1, version=V, headers=None, name=None):
    p = dict(params or {})
    p['_meta'] = {'io.modelcontextprotocol/protocolVersion': version,
                  'io.modelcontextprotocol/clientInfo': {'name': 't', 'version': '0'},
                  'io.modelcontextprotocol/clientCapabilities': {}}
    h = {'mcp-protocol-version': version, 'mcp-method': method}
    if name is not None:
        h['mcp-name'] = name
    h.update(headers or {})
    body = {'jsonrpc': '2.0', 'method': method, 'params': p}
    if msg_id is not None:
        body['id'] = msg_id
    return h, json.dumps(body).encode()


def legacy(method, params=None, msg_id=1, headers=None):
    body = {'jsonrpc': '2.0', 'method': method}
    if params is not None:
        body['params'] = params
    if msg_id is not None:
        body['id'] = msg_id
    return dict(headers or {}), json.dumps(body).encode()


class Base(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {'HENCE_MCP_ACTIVE': '1', 'HENCE_PUBLIC_BASE': 'https://app.example'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.up = StubUpstream(standard_routes())

    def run_(self, pair):
        return protocol.handle(pair[0], pair[1], self.up)


class Modern(Base):
    def test_discover(self):
        status, out = self.run_(modern('server/discover'))
        self.assertEqual(status, 200)
        r = out['result']
        self.assertEqual(r['resultType'], 'complete')
        self.assertEqual(r['supportedVersions'][0], V)
        self.assertEqual(r['capabilities'], {'tools': {'listChanged': False}})
        self.assertEqual(r['_meta']['io.modelcontextprotocol/serverInfo']['name'], 'hence')
        self.assertIn('read-only', r['instructions'])
        self.assertEqual((r['cacheScope'], isinstance(r['ttlMs'], int)), ('public', True))

    def test_tools_list_is_cacheable_and_stable(self):
        _, a = self.run_(modern('tools/list'))
        _, b = self.run_(modern('tools/list', msg_id=2))
        self.assertEqual(a['result']['tools'], b['result']['tools'])
        self.assertEqual([t['name'] for t in a['result']['tools']], [t['name'] for t in tools.TOOLS])
        self.assertEqual((a['result']['resultType'], a['result']['cacheScope']), ('complete', 'public'))
        self.assertNotIn('handler', json.dumps(a))

    def test_tools_call(self):
        status, out = self.run_(modern('tools/call', {'name': 'get_thesis', 'arguments': {'thesis_id': T1}}, name='get_thesis'))
        self.assertEqual(status, 200)
        r = out['result']
        self.assertEqual((r['resultType'], r['isError']), ('complete', False))
        self.assertEqual(r['structuredContent']['thesis_id'], T1)
        self.assertEqual(json.loads(r['content'][0]['text']), r['structuredContent'])   # the text block is the same data

    def test_a_tool_failure_is_a_result_the_model_can_read(self):
        status, out = self.run_(modern('tools/call', {'name': 'get_thesis', 'arguments': {'thesis_id': 'nope'}}, name='get_thesis'))
        self.assertEqual(status, 200)
        self.assertIs(out['result']['isError'], True)
        self.assertNotIn('structuredContent', out['result'])
        self.assertIn('thesis_id', out['result']['content'][0]['text'])

    def test_a_bug_in_a_tool_never_leaks_and_never_passes_as_data(self):
        with mock.patch.dict(tools.BY_NAME, {'get_thesis': dict(tools.BY_NAME['get_thesis'], handler=lambda a, u: 1 / 0)}):
            _, out = self.run_(modern('tools/call', {'name': 'get_thesis', 'arguments': {}}, name='get_thesis'))
        self.assertIs(out['result']['isError'], True)
        self.assertNotIn('ZeroDivision', json.dumps(out))

    def test_unknown_tool_and_bad_arguments_are_protocol_errors(self):
        _, out = self.run_(modern('tools/call', {'name': 'place_order', 'arguments': {}}, name='place_order'))
        self.assertEqual(out['error']['code'], -32602)
        _, out = self.run_(modern('tools/call', {'name': 'get_thesis', 'arguments': []}, name='get_thesis'))
        self.assertEqual(out['error']['code'], -32602)

    def test_headers_must_mirror_the_body(self):
        cases = [
            modern('tools/list', headers={'mcp-protocol-version': '2025-11-25'}),
            modern('tools/list', headers={'mcp-method': 'tools/call'}),
            modern('tools/call', {'name': 'get_thesis', 'arguments': {}}, name='search_theses'),
            modern('tools/call', {'name': 'get_thesis', 'arguments': {}}),                 # Mcp-Name missing
        ]
        h, b = modern('tools/list')
        h.pop('mcp-protocol-version')
        cases.append((h, b))
        for pair in cases:
            status, out = self.run_(pair)
            self.assertEqual((status, out['error']['code']), (400, -32020), pair[0])
        self.assertEqual(self.up.calls, [])

    def test_mcp_name_may_be_base64_encoded(self):
        enc = '=?base64?' + base64.b64encode(b'search_markets').decode() + '?='
        status, out = self.run_(modern('tools/call', {'name': 'search_markets', 'arguments': {'kind': 'perp'}}, name=enc))
        self.assertEqual((status, out['result']['isError']), (200, False))

    def test_an_unsupported_version_names_the_supported_ones(self):
        status, out = self.run_(modern('tools/list', version='2030-01-01'))
        self.assertEqual((status, out['error']['code']), (400, -32022))
        self.assertEqual(out['error']['data'], {'supported': list(protocol.SUPPORTED), 'requested': '2030-01-01'})

    def test_an_unknown_method_is_a_404_with_a_json_rpc_body(self):
        for method in ('resources/list', 'prompts/list', 'ping', 'initialize'):
            status, out = self.run_(modern(method))
            self.assertEqual((status, out['error']['code']), (404, -32601), method)

    def test_a_notification_is_accepted_with_no_body(self):
        self.assertEqual(self.run_(modern('notifications/cancelled', msg_id=None)), (202, None))


class Legacy(Base):
    def test_initialize_echoes_a_version_we_speak(self):
        for asked, got in (('2025-06-18', '2025-06-18'), ('2025-03-26', '2025-03-26'), ('2024-11-05', '2025-11-25'), (None, '2025-11-25')):
            status, out = self.run_(legacy('initialize', {'protocolVersion': asked, 'capabilities': {}, 'clientInfo': {'name': 't', 'version': '0'}}))
            self.assertEqual(status, 200)
            r = out['result']
            self.assertEqual(r['protocolVersion'], got)
            self.assertEqual(r['serverInfo']['name'], 'hence')
            self.assertNotIn('resultType', r)

    def test_the_handshake_then_list_and_call(self):
        h = {'mcp-protocol-version': '2025-06-18'}
        self.assertEqual(self.run_(legacy('notifications/initialized', msg_id=None, headers=h)), (202, None))
        _, out = self.run_(legacy('tools/list', headers=h))
        self.assertEqual(len(out['result']['tools']), len(tools.TOOLS))
        self.assertNotIn('resultType', out['result'])
        _, out = self.run_(legacy('tools/call', {'name': 'search_markets', 'arguments': {'query': 'btc', 'kind': 'perp'}}, headers=h))
        self.assertEqual(out['result']['structuredContent']['markets'][0]['market_id'], 'perp:BTC')
        self.assertEqual(self.run_(legacy('ping', headers=h))[1]['result'], {})

    def test_a_client_older_than_the_version_header_still_works(self):
        _, out = self.run_(legacy('tools/list'))                    # 2025-03-26 clients send no header
        self.assertIn('tools', out['result'])

    def test_an_unknown_method_is_a_json_rpc_error(self):
        status, out = self.run_(legacy('resources/list'))
        self.assertEqual((status, out['error']['code']), (200, -32601))

    def test_a_modern_header_without_the_body_version_is_refused_not_guessed(self):
        status, out = self.run_(legacy('tools/list', headers={'mcp-protocol-version': V}))
        self.assertEqual((status, out['error']['code']), (400, -32020))

    def test_an_unknown_header_version_is_refused(self):
        status, out = self.run_(legacy('tools/list', headers={'mcp-protocol-version': '1999-01-01'}))
        self.assertEqual((status, out['error']['code']), (400, -32022))


class Malformed(Base):
    def test_not_json_a_batch_and_not_json_rpc(self):
        self.assertEqual(protocol.handle({}, b'{nope', self.up)[1]['error']['code'], -32700)
        status, out = protocol.handle({}, json.dumps([{'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}]).encode(), self.up)
        self.assertEqual((status, out['error']['code']), (400, -32600))
        for bad in ({'id': 1, 'method': 'ping'}, {'jsonrpc': '2.0', 'id': 1}, {'jsonrpc': '2.0', 'id': 1, 'method': 5},
                    {'jsonrpc': '2.0', 'id': {'a': 1}, 'method': 'ping'}, {'jsonrpc': '2.0', 'id': True, 'method': 'ping'}, 'x', 5):
            status, out = protocol.handle({}, json.dumps(bad).encode(), self.up)
            self.assertEqual((status, out['error']['code']), (400, -32600), bad)
        status, out = protocol.handle({}, json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': [1]}).encode(), self.up)
        self.assertEqual((status, out['error']['code']), (400, -32602))


class KillSwitch(unittest.TestCase):
    def test_only_a_strict_1_opens_the_tools(self):
        up = StubUpstream(standard_routes())
        for value in ('0', '', 'true', 'yes', ' 1x'):
            with mock.patch.dict(os.environ, {'HENCE_MCP_ACTIVE': value}):
                for pair in (modern('tools/list'), legacy('initialize', {'protocolVersion': '2025-06-18'}),
                             modern('tools/call', {'name': 'search_markets', 'arguments': {}}, name='search_markets')):
                    status, out = protocol.handle(pair[0], pair[1], up)
                    self.assertEqual(status, 503, value)
                    self.assertIn('not open', out['error']['message'])
        self.assertEqual(up.calls, [])
        env = {k: v for k, v in os.environ.items() if k != 'HENCE_MCP_ACTIVE'}
        with mock.patch.dict(os.environ, env, clear=True):               # unset = closed
            self.assertEqual(protocol.handle(*modern('tools/list'), up)[0], 503)


if __name__ == '__main__':
    unittest.main()
