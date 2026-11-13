"""Settings, read from the environment on every call, so a changed setting needs no restart
logic of its own and a test can set one without reloading anything."""
import os

VERSION = '0.1.0'
SERVER_NAME = 'hence'
SERVER_TITLE = 'Hence'


def _env(name, default=''):
    return (os.environ.get(name) or default).strip()


def active():
    """The kill switch. Strictly '1' opens the tools; anything else answers every MCP request
    with "not open yet" while /healthz stays green, so the container can run dark."""
    return _env('HENCE_MCP_ACTIVE', '0') == '1'


def app_base():
    """Where the Hence app is read from: its public address by default. A deployment that
    sits beside the app can point this at the app's private address instead; a developer can
    point it at a local app."""
    return _env('HENCE_APP_BASE', 'https://app.hence.markets').rstrip('/')


def public_base():
    """The address a person opens. Every link a tool returns starts here."""
    return _env('HENCE_PUBLIC_BASE', 'https://app.hence.markets').rstrip('/')


def host():
    return _env('HENCE_MCP_HOST', '0.0.0.0')


def port():
    return int(_env('HENCE_MCP_PORT', '4340'))


def rate_per_minute():
    """Requests per client address per minute. Hosted assistants call from a few shared
    addresses, so this is a flood guard, not a per-person quota: per-person limits need the
    identity layer and belong to the app."""
    return max(1, int(_env('HENCE_MCP_RATE_PER_MIN', '300')))


def allowed_origins():
    """Browser origins that may call the endpoint. Server-side clients (Claude, ChatGPT, Grok,
    coding agents) send no Origin and are not affected. Localhost is always allowed so a
    developer's inspector works."""
    raw = _env('HENCE_MCP_ALLOWED_ORIGINS', public_base())
    return {o.strip().rstrip('/').lower() for o in raw.split(',') if o.strip()}


def trusted_client_header():
    """The header a trusted proxy in front of this server uses to state the real caller, e.g.
    CF-Connecting-IP behind Cloudflare. Empty (the default) = trust nothing and use the
    connection's own address: a header anyone can send must not pick the rate-limit key."""
    return _env('HENCE_MCP_CLIENT_IP_HEADER')


def data_dir():
    """Where data/market-display.json lives (display names only)."""
    explicit = _env('HENCE_MCP_DATA_DIR')
    if explicit:
        return explicit
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data'))


MAX_BODY_BYTES = 256 * 1024
UPSTREAM_TIMEOUT_S = 20
