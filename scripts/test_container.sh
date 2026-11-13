#!/bin/sh
# Smoke test of the built image (hence-mcp-server:check), as CI runs it.
# It proves three things about the image itself, which unit tests cannot:
#   1. it starts, as a non-root user, with nothing but the standard library;
#   2. with the switch off it stays healthy and refuses every MCP request;
#   3. with the switch on and the app unreachable, a tool call says so (isError) and returns no data.
set -eu

IMAGE="${HENCE_MCP_IMAGE:-hence-mcp-server:check}"
PORT="${HENCE_MCP_TEST_PORT:-44340}"
NAME="hence-mcp-server-check-$$"

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

run() { # $1 = HENCE_MCP_ACTIVE value
  cleanup
  # 127.0.0.1:9 inside the container: nothing listens there, so every app read fails fast
  docker run -d --name "$NAME" -p "127.0.0.1:$PORT:4340" \
    -e HENCE_MCP_ACTIVE="$1" -e HENCE_APP_BASE=http://127.0.0.1:9 "$IMAGE" >/dev/null
  i=0
  until python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$PORT/healthz', timeout=2)" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -gt 40 ]; then echo "the server did not become healthy" >&2; docker logs "$NAME" >&2 || true; exit 1; fi
    sleep 0.5
  done
}

[ "$(docker run --rm "$IMAGE" id -u)" != "0" ] || { echo "the image runs as root" >&2; exit 1; }

run 0
PORT="$PORT" python3 - <<'PY'
import json, os, urllib.error, urllib.request
url = 'http://127.0.0.1:%s/mcp' % os.environ['PORT']
V = '2026-07-28'
body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {'_meta': {'io.modelcontextprotocol/protocolVersion': V}}}).encode()
req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json', 'MCP-Protocol-Version': V, 'Mcp-Method': 'tools/list'})
try:
    urllib.request.urlopen(req, timeout=5)
    raise SystemExit('switch off: tools/list was answered; it must be refused')
except urllib.error.HTTPError as e:
    assert e.code == 503, e.code
    assert 'not open' in json.loads(e.read())['error']['message']
print('switch off: healthy and closed')
PY

run 1
PORT="$PORT" python3 - <<'PY'
import json, os, urllib.request
url = 'http://127.0.0.1:%s/mcp' % os.environ['PORT']
V = '2026-07-28'
def call(method, params, name=None):
    headers = {'Content-Type': 'application/json', 'MCP-Protocol-Version': V, 'Mcp-Method': method}
    if name:
        headers['Mcp-Name'] = name
    params = dict(params, _meta={'io.modelcontextprotocol/protocolVersion': V})
    req = urllib.request.Request(url, data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode(), headers=headers)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())['result']
tools = call('tools/list', {})['tools']
assert [t['name'] for t in tools] == ['search_theses', 'get_thesis', 'get_featured_theses', 'search_markets', 'get_thesis_guide'], tools
assert all(t['annotations']['readOnlyHint'] is True for t in tools)
for name, args in (('search_markets', {'kind': 'perp'}), ('search_theses', {}), ('get_featured_theses', {})):
    r = call('tools/call', {'name': name, 'arguments': args}, name)
    assert r['isError'] is True and 'structuredContent' not in r, (name, r)
print('switch on, app unreachable: 4 read-only tools, every call reports the failed read')
PY
