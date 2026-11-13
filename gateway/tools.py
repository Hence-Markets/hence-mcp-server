"""The tools an agent can call. Four of them, all reads.

A tool handler takes validated arguments and an Upstream, and returns a plain dict. It raises
ToolError for anything the calling model can act on (a bad argument, a thesis that is not
public, Hence not answering); the protocol layer turns that into an error result the model
reads. A handler never returns an empty list in place of a failed read: when one of two sources
fails, the result says which one and is marked partial.

Why so few tools: every tool's description is paid for in the caller's context on every turn,
and a model picks better among four clear tools than among forty thin ones.
"""
import base64
import json
import os

from . import config, shape
from . import appauth
from .upstream import Expired, NotFound, Unavailable

WORKSPACE = '/api/thesis-workspace'


class ToolError(Exception):
    """A failure to tell the model about, in a sentence it can act on."""


# ── argument reading ────────────────────────────────────────────────────────────────────────

def _string(args, key, limit, default=''):
    v = args.get(key, default)
    if v is None:
        return default
    if not isinstance(v, str):
        raise ToolError('`%s` must be text.' % key)
    v = v.strip()
    if len(v) > limit:
        raise ToolError('`%s` is too long (at most %d characters).' % (key, limit))
    return v


def _integer(args, key, low, high, default):
    v = args.get(key, default)
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, int):
        raise ToolError('`%s` must be a whole number.' % key)
    if not low <= v <= high:
        raise ToolError('`%s` must be between %d and %d.' % (key, low, high))
    return v


def _choice(args, key, allowed, default):
    v = args.get(key, default)
    if v is None:
        return default
    if v not in allowed:
        raise ToolError('`%s` must be one of: %s.' % (key, ', '.join(str(a) for a in allowed)))
    return v


def _boolean(args, key, default):
    v = args.get(key, default)
    if v is None:
        return default
    if not isinstance(v, bool):
        raise ToolError('`%s` must be true or false.' % key)
    return v


def _unknown_keys(args, schema):
    extra = sorted(set(args) - set(schema['properties']))
    if extra:
        raise ToolError('Unknown argument: %s.' % ', '.join(extra))


# ── results pages ───────────────────────────────────────────────────────────────────────────
# The app pages a search through a saved selection ("snapshot") so page 2 is page 2 of the same
# list. The cursor carries that selection and the filters it was made with; the app refuses a
# cursor whose filters changed, and one older than 12 hours.

def _cursor_pack(snapshot, offset, filters):
    raw = json.dumps({'s': snapshot, 'o': offset, 'f': filters}, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def _cursor_unpack(cursor, keys=('q', 'sort', 'venue')):
    try:
        raw = base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4))
        c = json.loads(raw.decode())
        s, o, f = c['s'], c['o'], c['f']
        if not (isinstance(s, str) and 0 < len(s) <= 64 and isinstance(o, int) and not isinstance(o, bool)
                and 0 <= o <= 100000 and isinstance(f, dict)
                and all(isinstance(k, str) and isinstance(v, str) and len(v) <= 200 for k, v in f.items())
                and set(f) <= set(keys)):
            raise ValueError
        return s, o, f
    except Exception:
        raise ToolError('That `cursor` is not valid. Search again without it.')


# ── tools ───────────────────────────────────────────────────────────────────────────────────

SEARCH_THESES = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'query': {'type': 'string', 'maxLength': 200,
                  'description': 'Words to look for in thesis titles and text, e.g. "AI capex", "oil", "rate cuts". Leave out to browse.'},
        'basket': {'type': 'string', 'enum': ['all', 'perps', 'spot'], 'default': 'all',
                   'description': 'Keep theses that can be bought mostly as perps, or mostly as spot tokens. "all" keeps every thesis.'},
        'sort': {'type': 'string', 'enum': ['relevant', 'latest', 'return_30d'], 'default': 'relevant',
                 'description': '"return_30d" orders by the last 30 days of hypothetical price history.'},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 12, 'default': 6},
        'cursor': {'type': 'string', 'maxLength': 600,
                   'description': 'Pass `next_cursor` from a previous result to get the next page of the same search.'},
    },
}


def search_theses(args, up):
    _unknown_keys(args, SEARCH_THESES)
    limit = _integer(args, 'limit', 1, 12, 6)
    cursor = _string(args, 'cursor', 600)
    if cursor:
        snapshot, offset, filters = _cursor_unpack(cursor)
    else:
        snapshot, offset = None, 0
        filters = {
            'q': _string(args, 'query', 200),
            'sort': {'relevant': '', 'latest': 'latest', 'return_30d': 'return_30d'}[
                _choice(args, 'sort', ('relevant', 'latest', 'return_30d'), 'relevant')],
            'venue': {'all': 'all', 'perps': 'perp', 'spot': 'spot'}[
                _choice(args, 'basket', ('all', 'perps', 'spot'), 'all')],
        }
    query = {'view': 'discover', 'venue': filters.get('venue', 'all'), 'q': filters.get('q', ''),
             'sort': filters.get('sort', ''), 'limit': limit, 'offset': offset, 'snapshot': snapshot}
    try:
        data = up.get_json(WORKSPACE + '/catalogue', query, ttl=60)
    except Expired:
        raise ToolError('That page of results has expired. Search again without `cursor`.')
    except NotFound:
        raise ToolError('Hence theses are not open to agents right now.')
    except Unavailable as e:
        raise ToolError('%s. No theses were read; try again shortly.' % e)
    items = data.get('items')
    if not isinstance(items, list):
        raise ToolError('Hence gave an answer that could not be read. No theses were read.')
    base = config.public_base()
    out = {
        'theses': [shape.thesis_card(r, base) for r in items if isinstance(r, dict)],
        'total_matching': shape.num(data.get('total')),
        'next_cursor': None,
        'as_of': shape.text(data.get('as_of')),
        'note': shape.DISCLAIMER,
    }
    nxt = data.get('next_offset')
    if isinstance(nxt, int) and not isinstance(nxt, bool) and isinstance(data.get('snapshot'), str):
        out['next_cursor'] = _cursor_pack(data['snapshot'], nxt, filters)
    if data.get('coverage_partial') is True:
        # the app could not read one venue's listings, so a basket filter may have dropped theses
        out['incomplete'] = 'One venue\'s listings could not be read, so this list may be missing theses.'
    return out


GET_THESIS = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['thesis_id'],
    'properties': {
        'thesis_id': {'type': 'string', 'description': 'The `thesis_id` from search_theses or get_featured_theses.'},
        'period_days': {'type': 'integer', 'enum': [30, 90, 180, 365], 'default': 90,
                        'description': 'Window for the hypothetical price history.'},
        'include_performance': {'type': 'boolean', 'default': True},
        'include_paper': {'type': 'boolean', 'default': False,
                          'description': 'Also list the paper basket: names Hence cannot trade, followed on paper. Often long.'},
        'include_related': {'type': 'boolean', 'default': False,
                            'description': 'Also list a few theses on nearby subjects.'},
    },
}


def get_thesis(args, up):
    _unknown_keys(args, GET_THESIS)
    thesis_id = _string(args, 'thesis_id', 36)
    if not shape.UUID_RE.match(thesis_id):
        raise ToolError('`thesis_id` must be an id returned by search_theses or get_featured_theses.')
    thesis_id = thesis_id.lower()
    days = _choice(args, 'period_days', (30, 90, 180, 365), 90)
    want_perf = _boolean(args, 'include_performance', True)
    want_paper = _boolean(args, 'include_paper', False)
    want_related = _boolean(args, 'include_related', False)
    path = '%s/theses/%s' % (WORKSPACE, thesis_id)
    try:
        detail = up.get_json(path, ttl=60)
    except (NotFound, Expired):
        raise ToolError('There is no public thesis with that id. It may be private or removed.')
    except Unavailable as e:
        raise ToolError('%s. The thesis was not read; try again shortly.' % e)
    if not isinstance(detail, dict) or not isinstance(detail.get('body'), dict):
        raise ToolError('Hence gave an answer that could not be read. The thesis was not read.')
    base = config.public_base()
    out = shape.thesis_detail(detail, base, with_paper=want_paper)
    out['note'] = shape.DISCLAIMER
    out['how_to_trade'] = 'Open the url in Hence to review and buy this basket. This server cannot place trades.'
    # The price history is a second read. If it fails the thesis is still worth returning, but
    # the result must say the figures are missing rather than leave them out silently.
    if want_perf:
        try:
            perf = shape.performance(up.get_json(path + '/performance', {'days': days, 'live': 1}, ttl=300), days)
            if perf is None:
                raise Unavailable('Hence gave an answer that could not be read')
            out['performance'] = perf
        except (Unavailable, NotFound, Expired) as e:
            out['performance'] = None
            out['performance_unavailable'] = 'The price history could not be read (%s). Do not state a return for this thesis.' % (
                e if isinstance(e, Unavailable) else 'not available')
    if want_related:
        try:
            rel = up.get_json(path + '/related', ttl=300)
            out['related'] = [{'thesis_id': r.get('id'), 'title': shape.text(r.get('title'), 200),
                               'url': shape.thesis_url(base, r.get('id'))}
                              for r in (rel.get('items') if isinstance(rel, dict) and isinstance(rel.get('items'), list) else [])[:5]
                              if isinstance(r, dict) and shape.UUID_RE.match(str(r.get('id') or ''))]
        except (Unavailable, NotFound, Expired):
            out['related'] = None
            out['related_unavailable'] = 'Related theses could not be read.'
    return out


FEATURED = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'topic': {'type': 'string', 'maxLength': 80,
                  'description': 'Keep one subject, e.g. "AI & compute", "Macro & rates", "Crypto". Leave out for all.'},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 12, 'default': 6},
    },
}


def get_featured_theses(args, up):
    _unknown_keys(args, FEATURED)
    limit = _integer(args, 'limit', 1, 12, 6)
    topic = _string(args, 'topic', 80).lower()
    try:
        data = up.get_json(WORKSPACE + '/discovery/features', ttl=300)
    except (NotFound, Expired):
        raise ToolError('Hence theses are not open to agents right now.')
    except Unavailable as e:
        raise ToolError('%s. Today\'s selection was not read; try again shortly.' % e)
    rows = data.get('candidates') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ToolError('Hence gave an answer that could not be read. Today\'s selection was not read.')
    base = config.public_base()
    cards = [shape.featured_card(r, base) for r in rows if isinstance(r, dict)]
    topics = sorted({c['topic'] for c in cards if c['topic']})
    if topic:
        cards = [c for c in cards if (c['topic'] or '').lower() == topic]
    out = {
        'edition': shape.text(data.get('edition')),
        'theses': cards[:limit],
        'topics': topics,
        'selection_rule': 'Public theses with a complete, positive one-month price history and cited sources. '
                          'A positive past month is how they were chosen, so it says nothing about what comes next.',
        'note': shape.DISCLAIMER,
    }
    if data.get('warming') is True:
        out['incomplete'] = 'Today\'s selection is still being prepared, so this list may be short.'
    return out


SEARCH_MARKETS = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'query': {'type': 'string', 'maxLength': 64,
                  'description': 'A ticker or name, e.g. "NVDA", "bitcoin", "OpenAI". Leave out for the most traded perps.'},
        'kind': {'type': 'string', 'enum': ['all', 'perp', 'spot'], 'default': 'all'},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 25, 'default': 10},
    },
}

_NAMES = None


def _display_names():
    """data/market-display.json: names for venue contracts whose ticker is not a name
    (io:OAI -> OpenAI). Missing file = no names, which is a display loss and nothing else."""
    global _NAMES
    if _NAMES is None:
        names = {}
        try:
            with open(os.path.join(config.data_dir(), 'market-display.json'), encoding='utf-8') as f:
                contracts = json.load(f).get('contracts')
            if isinstance(contracts, dict):
                names = contracts
        except Exception:
            names = {}
        _NAMES = names
    return _NAMES


def search_markets(args, up):
    _unknown_keys(args, SEARCH_MARKETS)
    query = _string(args, 'query', 64)
    kind = _choice(args, 'kind', ('all', 'perp', 'spot'), 'all')
    limit = _integer(args, 'limit', 1, 25, 10)
    base = config.public_base()
    names = _display_names()
    found, failed, as_of = [], {}, None
    if kind in ('all', 'perp'):
        try:
            data = up.get_json('/api/screener/hl', ttl=30)
            rows = data.get('rows') if isinstance(data, dict) else None
            if not isinstance(rows, dict):
                raise Unavailable('Hence gave an answer that could not be read')
            as_of = shape.num(data.get('ts'))
            for coin, row in rows.items():
                if isinstance(coin, str) and isinstance(row, dict):
                    m = shape.perp_market(coin, row, names, base)
                    score = shape.match_score(query, m['symbol'], m['name'])
                    if score:
                        found.append((score, m['volume_24h_usd'] or 0, m))
        except (Unavailable, NotFound, Expired) as e:
            failed['perp'] = str(e) if isinstance(e, Unavailable) else 'not available'
    if kind in ('all', 'spot') and (query or kind == 'spot'):
        # spot tokens are only listed for a search: without a query the perps' volume order
        # would bury them, and they carry no figure here to order by
        try:
            data = up.get_json('/api/screener/spot', ttl=120)
            rows = data.get('rows') if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise Unavailable('Hence gave an answer that could not be read')
            for row in rows:
                m = shape.spot_market(row, base) if isinstance(row, dict) else None
                if m:
                    score = shape.match_score(query, m['symbol'], m['name'])
                    if score:
                        found.append((score, 0, m))
        except (Unavailable, NotFound, Expired) as e:
            failed['spot'] = str(e) if isinstance(e, Unavailable) else 'not available'
    wanted = [k for k in ('perp', 'spot') if kind in ('all', k)]
    if failed and all(k in failed for k in wanted):
        raise ToolError('Hence markets could not be read (%s). No markets were read; try again shortly.'
                        % '; '.join('%s: %s' % kv for kv in sorted(failed.items())))
    found.sort(key=lambda t: (-t[0], -t[1], t[2]['market_id']))
    out = {
        'markets': [m for _, _, m in found[:limit]],
        'matches': len(found),
        'prices_as_of_unix': as_of,
        'coverage': 'Perps on Hyperliquid and its builder venues, and spot tokens on Robinhood Chain. '
                    'Solana and Elysium tokens and prediction markets are in the Hence app but not in this search yet.',
        'how_to_trade': 'Open a market\'s url in Hence to trade it. This server cannot place trades.',
    }
    if failed:
        # one of the two lists did not answer: say so, or a missing market reads as "not listed"
        out['incomplete'] = 'Not all markets were searched: %s could not be read.' % ' and '.join(sorted(failed))
    return out


ACCOUNT = {'type': 'object', 'additionalProperties': False, 'properties': {}}


def get_my_account(args, up, account):
    """The connected person's Hence username. `account` reads the app under their connection;
    its failures (not connected, not allowed, busy, unavailable) are raised and handled by the
    protocol layer, which knows how to ask the person to sign in."""
    _unknown_keys(args, ACCOUNT)
    data = account.get('/api/agents/v1/whoami')
    handle = shape.text(data.get('handle'))
    allowed = data.get('scopes')
    return {
        'username': handle,
        'profile_url': shape.text(data.get('profile_url')) if handle else None,
        'connected_agent': shape.text(data.get('agent')),
        'allowed': [s for s in allowed if isinstance(s, str)] if isinstance(allowed, list) else [],
        # null here is a fact (the app read the account and it has no username); a failed read never gets this far
        'note': None if handle else 'This Hence account has not claimed a username yet.',
    }


# ── the person's own theses and portfolio ─────────────────────────────────────────────────────
# These read the connected person's account through the app's signed surface (appauth). The app
# decides whose data it is from the connection; nothing here names an account or a wallet.

WHICH = ('created', 'saved', 'following', 'paper', 'archived', 'all')

LIST_MY_THESES = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'which': {'type': 'string', 'enum': list(WHICH), 'default': 'created',
                  'description': 'created: theses the person wrote (default). saved, following, paper (held on paper), archived, '
                                 'or all of the non-archived ones together.'},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 12, 'default': 10},
        'cursor': {'type': 'string', 'maxLength': 600, 'description': 'next_cursor from the previous page of the same list.'},
    },
}


def _account_dict(data, what):
    if not isinstance(data, dict):
        raise appauth.Unavailable('Hence gave an answer that could not be read (%s)' % what)
    return data


def list_my_theses(args, up, account):
    _unknown_keys(args, LIST_MY_THESES)
    which = _choice(args, 'which', WHICH, 'created')
    limit = _integer(args, 'limit', 1, 12, 10)
    cursor = _string(args, 'cursor', 600)
    query = {'filter': which, 'limit': limit}
    if cursor:
        snapshot, offset, made_with = _cursor_unpack(cursor, keys=('which',))
        if made_with.get('which') != which:
            raise ToolError('That `cursor` belongs to a different list. Ask again without it.')
        query.update(snapshot=snapshot, offset=offset)
    data = _account_dict(account.get('/api/agents/v1/theses', query), 'theses')
    rows = data.get('theses')
    if not isinstance(rows, list):
        raise appauth.Unavailable('Hence gave an answer that could not be read (theses)')
    base = config.public_base()
    out = {'theses': [shape.my_thesis_card(r, base) for r in rows if isinstance(r, dict)], 'which': which,
           'total': shape.num(data.get('total')), 'next_cursor': None, 'note': shape.DISCLAIMER}
    snapshot, nxt = data.get('snapshot'), data.get('next_offset')
    if isinstance(snapshot, str) and snapshot and isinstance(nxt, int) and not isinstance(nxt, bool):
        out['next_cursor'] = _cursor_pack(snapshot, nxt, {'which': which})
    unread = shape.num(data.get('unread'))
    if unread:
        # the page is short because rows could not be read, not because the list ended
        out['unread_count'] = unread
        out['unread_note'] = '%d theses on this page could not be read and are missing from it. The list is not complete.' % unread
    return out


GET_MY_THESIS = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['thesis_id'],
    'properties': {
        'thesis_id': {'type': 'string', 'description': 'The thesis id, from list_my_theses.'},
        'period_days': {'type': 'integer', 'enum': [30, 90, 180, 365], 'default': 90},
        'include_performance': {'type': 'boolean', 'default': True},
        'include_paper': {'type': 'boolean', 'default': False},
    },
}


def get_my_thesis(args, up, account):
    _unknown_keys(args, GET_MY_THESIS)
    thesis_id = _string(args, 'thesis_id', 64).lower()
    if not shape.UUID_RE.match(thesis_id):
        raise ToolError('`thesis_id` is not a thesis id. Use one from list_my_theses.')
    days = _choice(args, 'period_days', (30, 90, 180, 365), 90)
    want_perf = _boolean(args, 'include_performance', True)
    path = '/api/agents/v1/theses/' + thesis_id
    detail = _account_dict(account.get(path), 'thesis')
    if not isinstance(detail.get('body'), dict):
        raise appauth.Unavailable('Hence gave an answer that could not be read (thesis)')
    base = config.public_base()
    out = shape.thesis_detail(detail, base, with_paper=_boolean(args, 'include_paper', False))
    out.update(shape.ownership(detail))
    out['versions'] = [{'version': shape.num(v.get('version')), 'recorded_at': shape.text(v.get('created_at')),
                        'change': shape.text(v.get('change_reason'), 300)}
                       for v in (detail.get('versions') if isinstance(detail.get('versions'), list) else [])[:20] if isinstance(v, dict)]
    out['note'] = shape.DISCLAIMER
    out['how_to_trade'] = 'Open the url in Hence to review and buy this basket. This server cannot place trades.'
    if want_perf:
        # A second read. If it fails, or Hence says there is no history yet, the thesis is still
        # worth returning; the result says the figures are missing rather than leaving them out.
        try:
            perf = shape.performance(_account_dict(account.get(path + '/performance', {'days': days}), 'price history'), days)
            if perf is None:
                raise appauth.Unavailable('Hence gave an answer that could not be read')
            out['performance'] = perf
        except (appauth.Unavailable, appauth.Refused, appauth.Busy) as e:
            out['performance'] = None
            out['performance_unavailable'] = 'The price history could not be read (%s). Do not state a return for this thesis.' % (
                e if isinstance(e, appauth.Refused) else 'try again shortly')
    return out


GET_MY_PORTFOLIO = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'history_days': {'type': 'integer', 'minimum': 1, 'maximum': 365, 'default': 30,
                         'description': 'The window for the per-market trading results.'},
    },
}


def get_my_portfolio(args, up, account):
    """The person's open perpetual positions, recorded trading results and last recorded account
    value, as the Hence app answers them. Nothing is recomputed here: an unread section stays
    null and named, and `not_included` travels with every answer."""
    _unknown_keys(args, GET_MY_PORTFOLIO)
    days = _integer(args, 'history_days', 1, 365, 30)
    data = _account_dict(account.get('/api/agents/v1/portfolio', {'days': days}), 'portfolio')
    out = shape.portfolio(data)
    if out is None:
        raise appauth.Unavailable('Hence gave an answer that could not be read (portfolio)')
    return out


def _tool(name, title, description, schema, handler, scope=None):
    tool = {
        'name': name, 'title': title, 'description': description, 'inputSchema': schema,
        'annotations': {'title': title, 'readOnlyHint': True, 'destructiveHint': False,
                        'idempotentHint': True, 'openWorldHint': True},
        'handler': handler,
    }
    if scope:
        tool['scope'] = scope       # the permission the person must have given; its handler takes an account
    return tool


# Order is part of the contract: clients cache the list, and a stable order keeps their prompt cache warm.
TOOLS = [
    _tool('search_theses', 'Search Hence theses',
          'Search the public investment theses on Hence. A thesis is a stated belief about the world turned into a '
          'researched basket of holdings (perps, spot tokens, outcome markets) with reasons, sources and checkpoints. '
          'Returns cards with a link; call get_thesis for the full basket and its price history.',
          SEARCH_THESES, search_theses),
    _tool('get_thesis', 'Read a Hence thesis',
          'Read one public thesis in full: the claim, why now, what would prove it wrong, every holding with its '
          'weight and reason, the other baskets it can be bought as, its sources and checkpoints, and a hypothetical '
          'price history for the chosen window. If `performance` is null, do not state a return.',
          GET_THESIS, get_thesis),
    _tool('get_featured_theses', 'Today\'s featured theses',
          'Today\'s selection from Hence Discover: public theses with a complete one-month price history and cited '
          'sources. Use it when the person wants ideas rather than a specific subject.',
          FEATURED, get_featured_theses),
    _tool('search_markets', 'Search Hence markets',
          'Find what can be traded on Hence and where: perpetuals on Hyperliquid and its builder venues (crypto, '
          'stocks, indices, pre-IPO valuations, rates) with live price, 24h change, funding and open interest, and '
          'spot tokens on Robinhood Chain. A null figure means it was not read, not that it is zero.',
          SEARCH_MARKETS, search_markets),
]

BY_NAME = {t['name']: t for t in TOOLS}

# Tools that read a connected person's own account. They exist only while sign-in is switched
# on and configured (config.accounts_active); each names the permission it needs.
ACCOUNT_TOOLS = [
    _tool('get_my_account', 'Your Hence account',
          'The Hence username of the person using this agent. Needs their Hence account to be connected: '
          'if it is not, the client will ask them to sign in to Hence and allow it.',
          ACCOUNT, get_my_account, scope='account:read'),
    _tool('list_my_theses', 'Your theses',
          'The theses of the person using this agent: the ones they wrote (private ones included), saved, follow, hold '
          'on paper, or archived. Returns cards with a link; call get_my_thesis for one in full. If `unread_count` is '
          'present the list is not complete: say so. Needs their Hence account connected.',
          LIST_MY_THESES, list_my_theses, scope='theses:read'),
    _tool('get_my_thesis', 'Read one of your theses',
          'One thesis of the person using this agent in full, including a private one: the claim, every holding with '
          'its weight and reason, sources, checkpoints, versions, and a hypothetical price history. If `performance` '
          'is null, do not state a return. Needs their Hence account connected.',
          GET_MY_THESIS, get_my_thesis, scope='theses:read'),
    _tool('get_my_portfolio', 'Your positions and results',
          'What the person using this agent holds and has done on Hence: open perpetual positions read live from the '
          'venue, realized trading results from recorded fills, and the last account value Hence recorded (with its '
          'date). It is NOT the whole portfolio: `not_included` lists what is missing (cash, spot balances, outcome '
          'positions). Never present it as a total. If `positions.complete` is false or a section is in `unavailable`, '
          'say what could not be read; a null is unknown, never zero. Needs their Hence account connected.',
          GET_MY_PORTFOLIO, get_my_portfolio, scope='portfolio:read'),
]


def listed():
    """The tools this server offers right now, in a stable order."""
    return TOOLS + (ACCOUNT_TOOLS if config.accounts_active() else [])


def find(name):
    return next((t for t in listed() if t['name'] == name), None)


def public_definition(tool):
    """What tools/list sends: everything but the handler and the server-side permission."""
    return {k: v for k, v in tool.items() if k not in ('handler', 'scope')}
