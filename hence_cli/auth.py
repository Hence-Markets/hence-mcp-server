"""Signing this terminal in to a Hence account (OAuth 2.1, as MCP profiles it).

    hence login   opens Hence in the browser; the person presses Allow there; the answer comes
                  back to a port on this machine that only this process is listening on.

What is checked, because each one is a way this could be turned against the person:
  - The server says who signs people in (RFC 9728), and that document must be about THIS
    server. The sign-in service's own document must name itself as the issuer (RFC 8414).
  - Every address involved is https, or this machine.
  - PKCE (S256): the code that comes back is useless without a secret that never left here.
  - `state` must come back unchanged, and `iss` must be the service we sent the person to
    (RFC 9207): an answer from anywhere else is dropped.
  - The listener binds 127.0.0.1 only and answers exactly one path.

Nothing here prints a code, a token or a verifier.
"""
import base64
import hashlib
import hmac
import http.server
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser

from .client import LOOPBACK, USER_AGENT, CliError, Unreachable, send

REDIRECT = 'http://127.0.0.1/callback'       # registered without a port: a loopback redirect may use any (RFC 8252)
CLIENT_NAME = 'Hence CLI'


class SignInUnavailable(CliError):
    pass


def _safe(url):
    p = urllib.parse.urlsplit(url or '')
    host = (p.hostname or '').lower()
    return bool(host) and not p.username and not p.password and (p.scheme == 'https' or (p.scheme == 'http' and host in LOOPBACK))


def _get_json(url, sender):
    status, raw, _ = sender(url, 'GET', {'Accept': 'application/json', 'User-Agent': USER_AGENT}, None, 20)
    if status == 404:
        return None
    if status != 200:
        raise Unreachable('Hence could not be reached to sign in (HTTP %s). Nothing changed.' % status)
    try:
        data = json.loads(raw.decode('utf-8'))
    except Exception:
        data = None
    if not isinstance(data, dict):
        raise Unreachable('Hence gave a sign-in answer that could not be read. Nothing changed.')
    return data


def _post(url, fields, sender, as_json=False):
    body = json.dumps(fields).encode() if as_json else urllib.parse.urlencode(fields).encode()
    headers = {'Accept': 'application/json', 'User-Agent': USER_AGENT,
               'Content-Type': 'application/json' if as_json else 'application/x-www-form-urlencoded'}
    status, raw, _ = sender(url, 'POST', headers, body, 30)
    try:
        data = json.loads(raw.decode('utf-8')) if raw else {}
    except Exception:
        data = None
    return status, (data if isinstance(data, dict) else None)


def discover(server, sender=send):
    """Who signs people in for `server`, and where. Raises SignInUnavailable when the server
    does not offer sign-in at all."""
    p = urllib.parse.urlsplit(server)
    base = '%s://%s' % (p.scheme, p.netloc)
    meta = None
    for url in ([base + '/.well-known/oauth-protected-resource' + p.path] if p.path not in ('', '/') else []) + [base + '/.well-known/oauth-protected-resource']:
        meta = _get_json(url, sender)
        if meta:
            break
    if not meta:
        raise SignInUnavailable('This Hence server is not accepting sign-ins yet. Tools that do not need an account still work.')
    issuers = meta.get('authorization_servers')
    if (meta.get('resource') or '').rstrip('/') != server.rstrip('/') or not isinstance(issuers, list) or not issuers or not isinstance(issuers[0], str):
        raise CliError('The server\'s sign-in details do not match its address. Not signing in.')
    issuer = issuers[0].rstrip('/')
    if not _safe(issuer):
        raise CliError('The server names a sign-in service that is not on https. Not signing in.')
    doc = _get_json(issuer + '/.well-known/oauth-authorization-server', sender)
    if not doc or (doc.get('issuer') or '').rstrip('/') != issuer:
        raise CliError('The sign-in service did not confirm who it is. Not signing in.')
    out = {'issuer': issuer, 'scopes': [s for s in meta.get('scopes_supported') or [] if isinstance(s, str)],
           'iss_required': doc.get('authorization_response_iss_parameter_supported') is True}
    for key in ('authorization_endpoint', 'token_endpoint', 'registration_endpoint', 'revocation_endpoint'):
        value = doc.get(key)
        if key in ('authorization_endpoint', 'token_endpoint') and not isinstance(value, str):
            raise CliError('The sign-in service is missing its %s. Not signing in.' % key.replace('_', ' '))
        if value is not None and not (isinstance(value, str) and _safe(value)):
            raise CliError('The sign-in service gave an address that is not on https. Not signing in.')
        out[key] = value
    if 'S256' not in (doc.get('code_challenge_methods_supported') or []):
        raise CliError('The sign-in service does not support the protection this command requires (PKCE). Not signing in.')
    return out


def _client_id(server, where, store, sender):
    """This command's registration with the sign-in service, made once and reused."""
    entry = store.get(server)
    if entry.get('issuer') == where['issuer'] and isinstance(entry.get('client_id'), str) and entry['client_id']:
        return entry['client_id']
    if not where.get('registration_endpoint'):
        raise CliError('The sign-in service does not let this command register itself. Not signing in.')
    status, data = _post(where['registration_endpoint'], {
        'client_name': CLIENT_NAME, 'redirect_uris': [REDIRECT], 'grant_types': ['authorization_code', 'refresh_token'],
        'response_types': ['code'], 'token_endpoint_auth_method': 'none'}, sender, as_json=True)
    client_id = (data or {}).get('client_id')
    if status not in (200, 201) or not isinstance(client_id, str) or not client_id:
        raise Unreachable('Hence could not register this command (HTTP %s). Nothing changed.' % status)
    store.update(server, issuer=where['issuer'], client_id=client_id)
    return client_id


class _Callback(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):                       # never log the request line: it carries the code
        pass

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path != '/callback':
            self.send_error(404)
            return
        self.server.answer = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
        ok = 'code' in self.server.answer
        page = ('<!doctype html><meta charset="utf-8"><title>Hence</title><body style="font-family:system-ui;background:#0a0a0b;color:#f3f3f4;'
                'display:grid;place-items:center;height:100vh;margin:0"><div style="text-align:center"><h1 style="font-size:22px">%s</h1>'
                '<p style="color:#9a9aa2">%s</p></div>') % (
            ('Almost done' if ok else 'Nothing was connected'),
            ('Return to your terminal. You can close this tab.' if ok else 'You can close this tab.'))
        body = page.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _wait_for_answer(httpd, timeout):
    httpd.answer = None
    httpd.timeout = 1
    deadline = time.time() + timeout
    while httpd.answer is None and time.time() < deadline:
        httpd.handle_request()
    return httpd.answer


def _save_tokens(server, store, data, clock):
    access, refresh = data.get('access_token'), data.get('refresh_token')
    if not isinstance(access, str) or not access:
        raise Unreachable('Hence did not complete the sign-in. Run `hence login` again.')
    lifetime = data.get('expires_in')
    store.update(server, access_token=access, refresh_token=refresh if isinstance(refresh, str) else None,
                 expires_at=(clock() + lifetime) if isinstance(lifetime, (int, float)) and not isinstance(lifetime, bool) else None,
                 scope=data.get('scope') if isinstance(data.get('scope'), str) else '')


def login(server, store, sender=send, open_browser=webbrowser.open, say=print, timeout=300, clock=time.time):
    """Sign in. Returns the permissions the person allowed, as the server words them."""
    where = discover(server, sender)
    client_id = _client_id(server, where, store, sender)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    state = secrets.token_urlsafe(24)
    httpd = http.server.HTTPServer(('127.0.0.1', 0), _Callback)       # this machine only, a port the system picks
    try:
        redirect = 'http://127.0.0.1:%d/callback' % httpd.server_address[1]
        query = {'response_type': 'code', 'client_id': client_id, 'redirect_uri': redirect, 'code_challenge': challenge,
                 'code_challenge_method': 'S256', 'resource': server, 'state': state}
        if where['scopes']:
            query['scope'] = ' '.join(where['scopes'])
        url = where['authorization_endpoint'] + ('&' if '?' in where['authorization_endpoint'] else '?') + urllib.parse.urlencode(query)
        say('Opening Hence in your browser. Press Allow there to connect this terminal.')
        say('If it does not open, visit:\n  ' + url)
        threading.Thread(target=lambda: open_browser(url), daemon=True).start()
        answer = _wait_for_answer(httpd, timeout)
    finally:
        httpd.server_close()
    if answer is None:
        raise CliError('No answer came back within %d minutes. Nothing was connected.' % (timeout // 60))
    if not hmac.compare_digest(answer.get('state', ''), state):
        raise CliError('The answer did not match this sign-in. Nothing was connected.')
    if (where['iss_required'] or 'iss' in answer) and answer.get('iss', '').rstrip('/') != where['issuer']:
        raise CliError('The answer did not come from Hence\'s sign-in service. Nothing was connected.')
    if 'code' not in answer:
        if answer.get('error') == 'access_denied':
            raise CliError('You cancelled in Hence. Nothing was connected.')
        raise CliError('Hence did not allow the connection. Nothing was connected.')
    status, data = _post(where['token_endpoint'], {'grant_type': 'authorization_code', 'code': answer['code'], 'client_id': client_id,
                                                  'redirect_uri': redirect, 'code_verifier': verifier, 'resource': server}, sender)
    if status != 200 or data is None:
        raise Unreachable('Hence did not complete the sign-in (HTTP %s). Run `hence login` again.' % status)
    _save_tokens(server, store, data, clock)
    return store.get(server).get('scope') or ''


def refresh(server, store, sender=send, clock=time.time):
    """A fresh access token from the stored refresh token, or None when this terminal has to
    sign in again. The refresh token is single-use: the new pair replaces the old in one write."""
    entry = store.get(server)
    token, client_id, issuer = entry.get('refresh_token'), entry.get('client_id'), entry.get('issuer')
    if not (isinstance(token, str) and token and isinstance(client_id, str) and isinstance(issuer, str)):
        return None
    where = discover(server, sender)
    if where['issuer'] != issuer:
        return None
    status, data = _post(where['token_endpoint'], {'grant_type': 'refresh_token', 'refresh_token': token, 'client_id': client_id}, sender)
    if status == 200 and data is not None:
        _save_tokens(server, store, data, clock)
        return store.access_token(server)
    if status in (400, 401) and (data or {}).get('error') == 'invalid_grant':
        store.forget_tokens(server)                  # Hence says this sign-in is over
        return None
    # anything else is "Hence did not answer": keep the sign-in, it may be fine in a minute
    raise Unreachable('Hence could not renew this terminal\'s sign-in (HTTP %s). Try again shortly.' % status)


def logout(server, store, sender=send):
    """Disconnect: tell Hence (which closes the connection for both tokens), then forget
    them here. Returns True when Hence confirmed, False when only the local copy was dropped."""
    entry = store.get(server)
    token = entry.get('refresh_token') or entry.get('access_token')
    confirmed = False
    if isinstance(token, str) and token:
        try:
            where = discover(server, sender)
            if where.get('revocation_endpoint') and where['issuer'] == entry.get('issuer'):
                status, _ = _post(where['revocation_endpoint'], {'token': token}, sender)
                confirmed = status == 200
        except CliError:
            confirmed = False
    store.forget_tokens(server)
    return confirmed
