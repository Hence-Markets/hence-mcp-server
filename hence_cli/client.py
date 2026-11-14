"""One MCP server, spoken to over HTTP (the stateless 2026-07-28 protocol).

Every failure is a typed exception with a sentence a person can act on. Nothing here prints.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import VERSION

PROTOCOL = '2026-07-28'
META_VERSION = 'io.modelcontextprotocol/protocolVersion'
DEFAULT_SERVER = 'https://mcp.hence.markets/mcp'
# a plain urllib agent string is refused by some edge proxies before the server ever sees it
USER_AGENT = 'Mozilla/5.0 (compatible; hence-cli/%s)' % VERSION
LOOPBACK = ('127.0.0.1', 'localhost', '::1')


class CliError(Exception):
    """Something to tell the person. `exit_code` is the process's."""
    exit_code = 1


class Unreachable(CliError):
    pass


class ToolFailed(CliError):
    """The server answered, and the answer was "this could not be done"."""


class SignInNeeded(CliError):
    exit_code = 3

    def __init__(self, message, scope='', missing_permission=False):
        super().__init__(message)
        self.scope = scope
        self.missing_permission = missing_permission


def check_server(url):
    """The server address, normalised. https only, or http to this machine: a credential is
    never sent in the clear across a network."""
    try:
        p = urllib.parse.urlsplit(url or '')
        p.port
    except ValueError:
        raise CliError('"%s" is not a server address.' % url)
    host = (p.hostname or '').lower()
    if p.username or p.password or p.fragment or not host:
        raise CliError('"%s" is not a server address.' % url)
    if p.scheme != 'https' and not (p.scheme == 'http' and host in LOOPBACK):
        raise CliError('The server address must use https (http is accepted only for this machine).')
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path.rstrip('/') or '', p.query, ''))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer, never followed: urllib would re-send the request's headers (the person's
    credential, or this server's signature) to wherever the Location points, another host or plain http included."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def send(url, method, headers, body, timeout):
    """-> (status, body bytes, headers with lower-case names). Raises Unreachable. A redirect comes back
    as its own 3xx status: it is never followed."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, r.read(), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, (e.read() if hasattr(e, 'read') else b''), {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
    except Exception as e:
        raise Unreachable('Hence could not be reached (%s). Nothing was read.' % e.__class__.__name__)


def _challenge_field(header, name):
    m = re.search(r'%s="([^"]*)"' % re.escape(name), header or '')
    return m.group(1) if m else ''


class Client:
    def __init__(self, server=DEFAULT_SERVER, token=None, sender=send, timeout=60):
        self.server = check_server(server)
        self.token = token
        self._send = sender
        self._timeout = timeout

    def _rpc(self, method, params, name=None):
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': USER_AGENT,
                   'MCP-Protocol-Version': PROTOCOL, 'Mcp-Method': method}
        if name:
            headers['Mcp-Name'] = name
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': dict(params, _meta={META_VERSION: PROTOCOL})}).encode()
        status, raw, response_headers = self._send(self.server, 'POST', headers, body, self._timeout)
        try:
            data = json.loads(raw.decode('utf-8')) if raw else None
        except Exception:
            data = None
        said = ((data or {}).get('error') or {}).get('message') if isinstance(data, dict) else None
        challenge = response_headers.get('www-authenticate', '')
        if status == 401:
            raise SignInNeeded(said or 'This needs your Hence account.', scope=_challenge_field(challenge, 'scope'))
        if status == 403 and challenge:
            raise SignInNeeded(said or 'This needs a permission you have not allowed.', scope=_challenge_field(challenge, 'scope'),
                               missing_permission=True)
        if status == 429:
            wait = response_headers.get('retry-after', '')
            raise Unreachable('Hence is limiting requests from here. Try again in %s seconds.' % (wait if wait.isdigit() else '60'))
        if not isinstance(data, dict):
            raise Unreachable('Hence gave an answer that could not be read (HTTP %s). Nothing was read.' % status)
        if 'error' in data:
            raise ToolFailed(said or 'Hence refused the request.')
        result = data.get('result')
        if status != 200 or not isinstance(result, dict):
            raise Unreachable('Hence gave an answer that could not be read (HTTP %s). Nothing was read.' % status)
        return result

    def tools(self):
        found = self._rpc('tools/list', {}).get('tools')
        if not isinstance(found, list):
            raise Unreachable('Hence gave an answer that could not be read. Nothing was read.')
        return found

    def call(self, name, arguments):
        """A tool's structured answer. Raises ToolFailed with the server's own sentence when
        the tool says it could not do it: an error is never returned as a value."""
        result = self._rpc('tools/call', {'name': name, 'arguments': arguments or {}}, name)
        text = ' '.join(c.get('text', '') for c in result.get('content') or [] if isinstance(c, dict) and c.get('type') == 'text').strip()
        if result.get('isError'):
            raise ToolFailed(text or 'Hence could not do that.')
        value = result.get('structuredContent')
        if not isinstance(value, dict):
            raise Unreachable('Hence gave an answer that could not be read. Nothing was read.')
        return value
