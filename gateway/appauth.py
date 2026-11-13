"""The signed-in half: who a credential speaks for, and reads made under that connection.

A person connects their Hence account to an agent on Hence itself (OAuth 2.1; Hence is the
authorization server). The agent then sends this server a bearer token. This module does two
things with it, and nothing else:

  1. `connection(token)` asks the Hence app, once, who the token speaks for. The app answers
     only for a token issued for THIS server's address, and only while the person has not
     disconnected the agent.
  2. `get(grant, path)` reads the person's data from the app under that connection.

The person's token is never forwarded to the app's data routes (the protocol forbids passing a
client's token upstream). Every call this server makes is signed with its own secret instead:
HMAC-SHA256 over the method, path, connection, time, a nonce and the body's digest. The app
refuses anything unsigned, so a token that leaks is useless without this server.

Failures are typed, because they mean different things to the person:
  NotConnected  the credential is unknown, expired, revoked or for another server -> sign in
  MissingScope  the person did not allow this                                     -> ask again
  Busy          this connection is over its limit                                 -> wait
  Unavailable   Hence did not answer                                              -> try again
An unanswered check is never treated as "not connected": that would make the agent throw away
a good credential and send the person through sign-in again for nothing.
"""
import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.error
import urllib.request

from . import config

INTROSPECT = '/api/agents/v1/introspect'
# Every path this module may read under a connection. A new signed-in tool adds its path here,
# and tests/test_tools.py pins the list: this server can reach nothing it was not given.
ACCOUNT_PATHS = ('/api/agents/v1/whoami',)

CACHE_S = 60          # how long "this token is good" is remembered; the app re-checks the connection on every read anyway
MAX_CACHED = 10000


class NotConnected(Exception):
    pass


class MissingScope(Exception):
    def __init__(self, scope):
        super().__init__(scope)
        self.scope = scope


class Busy(Exception):
    def __init__(self, retry_after):
        super().__init__('retry in %ss' % retry_after)
        self.retry_after = retry_after


class Unavailable(Exception):
    pass


def sign(secret, method, path, grant, body, stamp=None, nonce=None):
    """The headers that prove a call came from this server. The Hence app computes the same
    value; tests in both repositories pin the same two test vectors."""
    stamp = str(int(time.time())) if stamp is None else str(stamp)
    nonce = secrets.token_hex(16) if nonce is None else nonce
    message = '\n'.join((method, path, grant or '', stamp, nonce, hashlib.sha256(body or b'').hexdigest())).encode()
    return {'X-Hence-Agent-Grant': grant or '', 'X-Hence-Agent-Time': stamp, 'X-Hence-Agent-Nonce': nonce,
            'X-Hence-Agent-Signature': hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()}


def _send(url, method, headers, body, timeout):
    """-> (status, body bytes, response headers). Raises OSError family on transport failure."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers.items())
    except urllib.error.HTTPError as e:
        return e.code, (e.read() if hasattr(e, 'read') else b''), dict(e.headers.items() if e.headers else {})


class AppAuth:
    """`send` and `clock` are injectable so tests need no network and no sleeping."""

    def __init__(self, base=None, send=_send, clock=time.time):
        self._base = base
        self._send = send
        self._clock = clock
        self._cache = {}
        self._lock = threading.Lock()

    def _call(self, method, path, grant, body):
        secret = config.gateway_secret()
        if not secret:
            raise Unavailable('sign-in is not set up on this server')
        headers = {'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0 (compatible; HenceMCP/%s)' % config.VERSION}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        headers.update(sign(secret, method, path, grant, body or b''))
        try:
            status, raw, response_headers = self._send((self._base or config.app_base()).rstrip('/') + path, method, headers, body,
                                                       config.UPSTREAM_TIMEOUT_S)
        except Exception as e:
            raise Unavailable('Hence could not be reached (%s)' % e.__class__.__name__)
        try:
            data = json.loads(raw.decode('utf-8')) if raw else {}
        except Exception:
            data = None
        return status, (data if isinstance(data, dict) else None), response_headers

    def connection(self, token):
        """{'grant', 'scopes', 'client_name'} for a live credential issued for this server.
        Raises NotConnected or Unavailable."""
        if not isinstance(token, str) or not token or len(token) > 256:
            raise NotConnected()
        key = hashlib.sha256(token.encode('utf-8')).hexdigest()       # the token itself is never kept
        now = self._clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1]
        body = json.dumps({'token': token, 'resource': config.resource()}).encode()
        status, data, _ = self._call('POST', INTROSPECT, '', body)
        if status != 200 or data is None:
            # includes a refused signature (our misconfiguration): not the person's problem to fix by signing in again
            raise Unavailable('Hence could not verify the sign-in (answered %s)' % status)
        if data.get('active') is not True:
            raise NotConnected()
        grant, scope = data.get('grant'), data.get('scope')
        if not isinstance(grant, str) or not grant or not isinstance(scope, str):
            raise Unavailable('Hence gave an answer that could not be read')
        info = {'grant': grant, 'scopes': scope.split(), 'client_name': data.get('client_name') if isinstance(data.get('client_name'), str) else None}
        ttl = min(CACHE_S, data['expires_in']) if isinstance(data.get('expires_in'), int) and not isinstance(data.get('expires_in'), bool) else CACHE_S
        if ttl > 0:
            with self._lock:
                if len(self._cache) >= MAX_CACHED:
                    for k in [k for k, v in self._cache.items() if v[0] <= now]:
                        self._cache.pop(k, None)
                    while len(self._cache) >= MAX_CACHED:
                        self._cache.pop(next(iter(self._cache)))
                self._cache[key] = (now + ttl, info)
        return info

    def forget(self, token):
        """Drop what is remembered about a token (the app just said its connection is gone)."""
        if isinstance(token, str):
            with self._lock:
                self._cache.pop(hashlib.sha256(token.encode('utf-8')).hexdigest(), None)

    def get(self, grant, path):
        """Read `path` under a connection. Only paths in ACCOUNT_PATHS can be asked for."""
        if path not in ACCOUNT_PATHS:
            raise ValueError('not an account path')
        status, data, headers = self._call('GET', path, grant, None)
        error = (data or {}).get('error')
        if status == 200 and data is not None:
            return data
        if status == 401 and error == 'invalid_token':
            raise NotConnected()
        if status == 403 and error == 'insufficient_scope':
            raise MissingScope(data.get('scope') if isinstance(data.get('scope'), str) else '')
        if status == 429:
            retry = next((v for k, v in headers.items() if k.lower() == 'retry-after'), '')
            raise Busy(retry if str(retry).isdigit() else '60')
        raise Unavailable('Hence answered %s' % status)
