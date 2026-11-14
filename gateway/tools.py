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
import re

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
                m = shape.spot_market(row, base, config.spot_figures()) if isinstance(row, dict) else None
                if m:
                    score = shape.match_score(query, m['symbol'], m['name'])
                    if score:
                        found.append((score, m.get('volume_24h_usd') or 0, m))
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
        'how_to_trade': 'Open a market\'s url in Hence to trade it. This server cannot place trades.'
                        + (' It can prepare one perpetual order for the person to review and sign in Hence: propose_trade.' if config.trades_active() else ''),
    }
    if failed:
        # one of the two lists did not answer: say so, or a missing market reads as "not listed"
        out['incomplete'] = 'Not all markets were searched: %s could not be read.' % ' and '.join(sorted(failed))
    return out


GET_ASSET = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['id'],
    'properties': {
        'id': {'type': 'string', 'maxLength': 80,
               'description': 'A perpetual\'s market_id from search_markets ("perp:xyz:NVDA", "perp:BTC"), or a plain ticker ("NVDA"). A US-listed company '
                              'that Hence does not trade can be looked up by its ticker; write "ALT.US" for a company whose ticker is also a crypto.'},
        'sections': {'type': 'array', 'maxItems': 9, 'items': {'type': 'string', 'enum': list(shape.ASSET_SECTIONS)},
                     'description': 'What to read. Left out: quote, history and profile. analysts, insiders, earnings, news and fundamentals are for '
                                    'companies; analysis is Hence\'s own daily report, when one exists.'},
    },
}
_ASSET_ID = re.compile(r'^(perp:(?:[a-z0-9]{1,12}:)?[A-Za-z0-9._/-]{1,40}|[A-Za-z0-9.\-]{1,12})$')


def get_asset(args, up):
    _unknown_keys(args, GET_ASSET)
    asset_id = _string(args, 'id', 80)
    if not _ASSET_ID.match(asset_id):
        raise ToolError('`id` is a perpetual\'s market_id from search_markets (perp:xyz:NVDA) or a plain ticker (NVDA).')
    sections = args.get('sections')
    if sections is not None and (not isinstance(sections, list) or any(s not in shape.ASSET_SECTIONS for s in sections)):
        raise ToolError('`sections` is a list of: %s.' % ', '.join(shape.ASSET_SECTIONS))
    # asked in the answer's own order, so the same request is always the same address (and the same cached answer)
    query = {'id': asset_id, 'sections': ','.join(s for s in shape.ASSET_SECTIONS if s in set(sections or []))}
    try:
        data = up.get_json('/api/research/asset', query, ttl=120)
    except (NotFound, Expired):
        raise ToolError('Hence has no market and no research data for "%s". Find a market with search_markets, or check the ticker.' % asset_id)
    except Unavailable as e:
        raise ToolError('%s. The asset was not read; try again shortly.' % e)
    out = shape.asset_research(data, config.public_base())
    if out is None:
        raise ToolError('Hence gave an answer that could not be read. The asset was not read.')
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
        'include_spot': {'type': 'boolean', 'default': False,
                         'description': 'Also read Robinhood Chain tokens and wallet stablecoins. Slower and costlier (read on-chain for every wallet): '
                                        'ask only when the person wants their spot balances.'},
    },
}


def get_my_portfolio(args, up, account):
    """The person's open perpetual positions, recorded trading results and last recorded account
    value, as the Hence app answers them. Nothing is recomputed here: an unread section stays
    null and named, and `not_included` travels with every answer."""
    _unknown_keys(args, GET_MY_PORTFOLIO)
    days = _integer(args, 'history_days', 1, 365, 30)
    query = {'days': days}
    if _boolean(args, 'include_spot', False):
        query['include'] = 'spot'
    data = _account_dict(account.get('/api/agents/v1/portfolio', query), 'portfolio')
    out = shape.portfolio(data)
    if out is None:
        raise appauth.Unavailable('Hence gave an answer that could not be read (portfolio)')
    return out


# ── a thesis the agent writes itself ──────────────────────────────────────────────────────────
# The agent does the thinking, with its own model and sources, and hands Hence a thesis in the
# format below. Hence checks it against its own listings and, if the person agrees, puts it in
# their library as a private draft. The person opens it in Hence and saves it themselves: that
# save, not anything here, is what makes it a thesis. Hence's research pipeline is not involved
# and nothing here is labelled as Hence research.

THESIS_FORMAT = 'hence-thesis/1'
THESIS = {
    'type': 'object',
    'description': 'A thesis in the %s format. Read get_thesis_guide before writing one.' % THESIS_FORMAT,
    'required': ['format', 'title', 'summary', 'mechanism', 'why_now', 'counter_case', 'invalidation', 'horizon_days', 'holdings'],
    'properties': {
        'format': {'type': 'string', 'enum': [THESIS_FORMAT]},
        'title': {'type': 'string', 'maxLength': 180},
        'summary': {'type': 'string', 'maxLength': 1000},
        'mechanism': {'type': 'string', 'maxLength': 4000},
        'why_now': {'type': 'string', 'maxLength': 2000},
        'counter_case': {'type': 'string', 'maxLength': 2000},
        'invalidation': {'type': 'string', 'maxLength': 2000},
        'horizon_days': {'type': 'integer', 'minimum': 1, 'maximum': 10950},
        'topic': {'type': 'string', 'maxLength': 80},
        'cash_weight_pct': {'type': 'number', 'minimum': 0, 'exclusiveMaximum': 100},
        'holdings': {'type': 'array', 'minItems': 1, 'maxItems': 20, 'items': {
            'type': 'object', 'required': ['market_id', 'side', 'weight_pct', 'role', 'reason'], 'properties': {
                'market_id': {'type': 'string', 'description': 'Exactly as search_markets returned it.'},
                'side': {'type': 'string', 'enum': ['long', 'short']},
                'weight_pct': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 100},
                'role': {'type': 'string', 'enum': ['core', 'second_order', 'hedge']},
                'reason': {'type': 'string', 'maxLength': 1000}}}},
        'sources': {'type': 'array', 'maxItems': 30, 'items': {
            'type': 'object', 'required': ['title', 'url'], 'properties': {
                'title': {'type': 'string'}, 'url': {'type': 'string'}, 'publisher': {'type': 'string'},
                'published_at': {'type': 'string', 'description': 'YYYY-MM-DD'}}}},
        'checkpoints': {'type': 'array', 'maxItems': 5, 'items': {
            'type': 'object', 'required': ['by', 'observable', 'direction'], 'properties': {
                'by': {'type': 'string', 'description': 'YYYY-MM-DD, after today and within 12 months'},
                'observable': {'type': 'string', 'maxLength': 300},
                'direction': {'type': 'string', 'enum': ['supports', 'contradicts']}}}},
    },
}
GUIDE = {'type': 'object', 'additionalProperties': False, 'properties': {}}
CHECK_THESIS = {'type': 'object', 'additionalProperties': False, 'required': ['thesis'], 'properties': {'thesis': THESIS}}
SAVE_THESIS = {'type': 'object', 'additionalProperties': False, 'required': ['thesis'], 'properties': {
    'thesis': {'type': 'object', 'description': 'The same object check_thesis accepted, unchanged.'}}}

_GUIDE = None


def _guide_text():
    """data/write-a-thesis.md, read once. The same file ships in the skill (tests pin that the
    two copies are identical), so an agent reads one method whichever way it arrives."""
    global _GUIDE
    if _GUIDE is None:
        with open(os.path.join(config.data_dir(), 'write-a-thesis.md'), encoding='utf-8') as f:
            _GUIDE = f.read()
    return _GUIDE


def get_thesis_guide(args, up):
    _unknown_keys(args, GUIDE)
    try:
        guide = _guide_text()
    except OSError:
        raise ToolError('The guide could not be read on this server. Nothing else is affected.')
    return {'format': THESIS_FORMAT, 'guide': guide,
            'steps': ['search_markets for every holding and copy its market_id', 'check_thesis until it has no problems',
                      'show the person the thesis and ask', 'save_thesis_draft', 'give the person the url'],
            'note': 'A thesis you write is yours, not Hence research. Hence checks it and stores it as a private draft; '
                    'the person reviews it in Hence and saves it there. Nothing here places a trade.'}


def _thesis_arg(args, schema):
    _unknown_keys(args, schema)
    thesis = args.get('thesis')
    if not isinstance(thesis, dict):
        raise ToolError('`thesis` must be an object in the %s format. Read get_thesis_guide first.' % THESIS_FORMAT)
    return thesis


def check_thesis(args, up, account):
    """Hence's verdict on a thesis the agent wrote. Saves nothing."""
    out = shape.thesis_check(account.post('/api/agents/v1/theses/check', _thesis_arg(args, CHECK_THESIS)))
    if out is None:
        raise appauth.Unavailable('Hence gave an answer that could not be read (thesis check)')
    return out


def save_thesis_draft(args, up, account):
    """Put the thesis in the person's Hence library as a private draft. It is checked again
    first; a thesis with problems is not saved."""
    out = shape.thesis_check(account.post('/api/agents/v1/theses/drafts', _thesis_arg(args, SAVE_THESIS)))
    if out is None:
        raise appauth.Unavailable('Hence gave an answer that could not be read (thesis draft)')
    return out


# ── Hence's own research, started for the person ────────────────────────────────────────────
# The 20 to 40 minute build the app runs when a person types an idea. It is the person's run,
# counted against their daily limit, and the one tool here that spends Hence's money: it is
# offered only when its own switch is on (config.research_active).

START_RESEARCH = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['belief'],
    'properties': {
        'belief': {'type': 'string', 'minLength': 2, 'maxLength': 6000,
                   'description': 'The idea to research, in the person\'s own words where possible.'},
        'guidance': {'type': 'string', 'maxLength': 2000,
                     'description': 'How the person wants it built: constraints, what to emphasise, what to avoid.'},
        'must_include': {'type': 'array', 'maxItems': 12, 'items': {'type': 'string'},
                         'description': 'market_ids (from search_markets) that must be in the basket.'},
        'exclude': {'type': 'array', 'maxItems': 12, 'items': {'type': 'string'},
                    'description': 'market_ids (from search_markets) that must not be in the basket.'},
        'basket': {'type': 'string', 'enum': ['perps', 'spot'], 'description': 'Where to build it. Leave out and Hence picks.'},
    },
}
GET_RESEARCH = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {'run_id': {'type': 'string', 'description': 'From start_thesis_research. Leave out to list what is in progress and what recently finished.'}},
}


def start_thesis_research(args, up, account):
    _unknown_keys(args, START_RESEARCH)
    out = shape.research_start(account.post('/api/agents/v1/research', args))
    if out is None:
        raise appauth.Unavailable('Hence gave an answer that could not be read (research). Ask again: the same request reaches the same run')
    return out


def get_thesis_research(args, up, account):
    _unknown_keys(args, GET_RESEARCH)
    run_id = _string(args, 'run_id', 64).lower()
    if not run_id:
        out = shape.research_list(account.get('/api/agents/v1/research'))
    elif not shape.UUID_RE.match(run_id):
        raise ToolError('`run_id` is not a research run id. Use the one start_thesis_research returned.')
    else:
        out = shape.research_run(account.get('/api/agents/v1/research/' + run_id))
    if out is None:
        raise appauth.Unavailable('Hence gave an answer that could not be read (research)')
    return out


# ── a trade prepared for the person to review and sign in Hence ─────────────────────────────
# "Propose, I approve". The agent says what the trade is; Hence stores it as words; the person
# opens it in Hence, their own order ticket is filled in, and they review and sign it with
# their own wallet. No tool here places an order, and none could: this server holds no wallet,
# no key and no route to a venue. Offered only when its own switch is on (config.trades_active).

PROPOSE_TRADE = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['market_id', 'side', 'size_usd'],
    'properties': {
        'market_id': {'type': 'string', 'maxLength': 80,
                      'description': 'A perpetual, exactly as search_markets returned it (perp:BTC, perp:xyz:NVDA). Spot tokens and outcome markets cannot be proposed yet.'},
        'side': {'type': 'string', 'enum': ['long', 'short']},
        'size_usd': {'type': 'number', 'minimum': 10, 'maximum': 100000,
                     'description': 'The position\'s value in US dollars. This is the size of the position, not the margin: at 3x leverage a 300 position needs about 100 of margin.'},
        'leverage': {'type': 'integer', 'minimum': 1, 'maximum': 50,
                     'description': 'Leave out to keep the person\'s own setting for the market. The ticket lowers it to the market\'s maximum.'},
        'order_type': {'type': 'string', 'enum': ['market', 'limit'], 'description': 'Leave out for a market order.'},
        'limit_price': {'type': 'number', 'exclusiveMinimum': 0, 'description': 'Required for a limit order; leave out for a market order.'},
        'note': {'type': 'string', 'maxLength': 280, 'description': 'Why, in one or two sentences. The person sees it in Hence, marked as your words.'},
        'valid_for_hours': {'type': 'integer', 'minimum': 1, 'maximum': 72, 'description': 'How long the person has to act on it. Leave out for 24.'},
    },
}
PROPOSE_BASKET = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['thesis_id', 'amount_usd'],
    'properties': {
        'thesis_id': {'type': 'string', 'description': 'A thesis the person can open: one of theirs (list_my_theses) or a public one (search_theses).'},
        'amount_usd': {'type': 'number', 'minimum': 10, 'maximum': 100000,
                       'description': 'The money put into the basket, in US dollars and whole cents. With leverage the positions are worth more than this.'},
        'basket': {'type': 'string', 'enum': ['perps', 'spot'], 'description': 'Which of the thesis\'s baskets. Leave out and the thesis opens on its own.'},
        'leverage': {'type': 'integer', 'minimum': 1, 'maximum': 20, 'description': 'Perpetual baskets only. Leave out for none. The ticket lowers it to what the basket\'s markets allow.'},
        'note': {'type': 'string', 'maxLength': 280, 'description': 'Why, in one or two sentences. The person sees it in Hence, marked as your words.'},
        'valid_for_hours': {'type': 'integer', 'minimum': 1, 'maximum': 72, 'description': 'How long the person has to act on it. Leave out for 24.'},
    },
}
GET_PROPOSALS = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {'proposal_id': {'type': 'string', 'description': 'From propose_trade. Leave out to list the trades you prepared for this person.'}},
}


def propose_trade(args, up, account):
    _unknown_keys(args, PROPOSE_TRADE)
    out = shape.trade_propose(account.post('/api/agents/v1/trades/proposals', args))
    if out is None:
        raise appauth.Unavailable('Hence gave an answer that could not be read (proposed trade). Ask again: the same request reaches the same proposal')
    return out


def propose_basket(args, up, account):
    _unknown_keys(args, PROPOSE_BASKET)
    if not shape.UUID_RE.match(_string(args, 'thesis_id', 64)):
        raise ToolError('`thesis_id` is not a thesis id. Use the one a thesis tool returned.')
    out = shape.trade_propose(account.post('/api/agents/v1/trades/proposals', dict(args, thesis_id=args['thesis_id'].lower())))
    if out is None or (out.get('proposal') or {}).get('kind') not in (None, 'basket'):
        raise appauth.Unavailable('Hence gave an answer that could not be read (proposed basket). Ask again: the same request reaches the same proposal')
    return out


def get_trade_proposals(args, up, account):
    _unknown_keys(args, GET_PROPOSALS)
    proposal_id = _string(args, 'proposal_id', 64)
    if not proposal_id:
        out = shape.trade_list(account.get('/api/agents/v1/trades/proposals'))
    elif not shape.PROPOSAL_RE.match(proposal_id):
        raise ToolError('`proposal_id` is not a proposal id. Use the one propose_trade returned.')
    else:
        out = shape.trade_proposal(account.get('/api/agents/v1/trades/proposals/' + proposal_id))
    if out is None:
        raise appauth.Unavailable('Hence gave an answer that could not be read (proposed trade)')
    return out


def _tool(name, title, description, schema, handler, scope=None, writes=False):
    tool = {
        'name': name, 'title': title, 'description': description, 'inputSchema': schema,
        # writes: this tool changes something for the person (hosts ask before running it).
        # Saving the same thesis twice updates one draft, so it is idempotent; nothing is deleted.
        'annotations': {'title': title, 'readOnlyHint': not writes, 'destructiveHint': False,
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
          'spot tokens on Robinhood Chain with price, 24h change, volume and liquidity from Hence\'s latest snapshot '
          '(it can be hours old: say so). A null figure means it was not read, not that it is zero. For one asset '
          'in full, pass its ticker with `limit` 1.',
          SEARCH_MARKETS, search_markets),
    _tool('get_asset', 'Research one asset',
          'What Hence knows about one asset: its live price on each venue Hence trades it on, 90 days of daily prices, '
          'and for a listed company its profile, analyst ratings and price targets, insider trades, earnings, headlines '
          'and fundamentals, plus Hence\'s own daily analysis when there is one. Works for a market from search_markets '
          'and for a US-listed company Hence does not trade (research only). A null section named in `unavailable` was '
          'not read: say so. Prices are the venue\'s or a reference quote that may be delayed, as each says.',
          GET_ASSET, get_asset),
    _tool('get_thesis_guide', 'How to write a Hence thesis',
          'Read this before writing a thesis yourself: the method (claim, mechanism, exposures, weights, sources, '
          'checkpoints) and the %s format. Use it when the person has a view of their own or wants a basket shaped '
          'to their constraints, instead of asking Hence to research one.' % THESIS_FORMAT,
          GUIDE, get_thesis_guide),
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
          'venue, their Hyperliquid account value and available cash, outcome-market positions, realized trading '
          'results from recorded fills, and the last account value Hence recorded (with its date). With `include_spot`, '
          'also Robinhood Chain tokens and wallet stablecoins. It is NOT one total: `not_included` lists what is '
          'missing, and the parts must not be added together. If `positions.complete` is false, a wallet has entries '
          'in `unread`, or a section is in `unavailable`, say what could not be read; a null is unknown, never zero. '
          'Needs their Hence account connected.',
          GET_MY_PORTFOLIO, get_my_portfolio, scope='portfolio:read'),
    _tool('check_thesis', 'Check a thesis you wrote',
          'Have Hence check a thesis you wrote in the %s format (see get_thesis_guide). Saves nothing. Returns every '
          'problem at once, each naming the field to change, warnings the person should hear, and the thesis as Hence '
          'would store it, with each holding named from Hence\'s own listings. Fix the problems and check again.' % THESIS_FORMAT,
          CHECK_THESIS, check_thesis, scope='theses:write'),
    _tool('save_thesis_draft', 'Save a thesis as a draft',
          'Put a thesis you wrote into the person\'s Hence library as a private draft. Only after check_thesis has no '
          'problems AND the person has seen the thesis and agreed. It does not create, publish or trade anything: the '
          'person opens the returned url in Hence, reviews the draft and saves it there. It is labelled as written by '
          'you, not by Hence. Saving the same thesis again updates the same draft.',
          SAVE_THESIS, save_thesis_draft, scope='theses:write', writes=True),
    _tool('get_thesis_research', 'Follow Hence research',
          'The state of a research run Hence is doing for the person: queued, running (with what it is doing now), or '
          'finished, with the thesis and its url once there is one. Without a run_id, lists what is in progress and what '
          'recently finished. A run takes 20 to 40 minutes: check back, do not wait in a loop. Hence also notifies the '
          'person in the app when it finishes.',
          GET_RESEARCH, get_thesis_research, scope='theses:read'),
]

# Offered only while config.research_active(): the one tool that spends Hence's money.
RESEARCH_TOOLS = [
    _tool('start_thesis_research', 'Ask Hence to research an idea',
          'Ask Hence to research an idea and build a thesis for the person, the way the app does when they type one. It '
          'takes 20 to 40 minutes and counts toward the person\'s daily research limit, so ask them first. Steer it with '
          '`guidance`, `must_include`, `exclude` and `basket`. Returns a run_id: follow it with get_thesis_research. The '
          'finished thesis is Hence\'s research, saved to their account and shared like any thesis they create. To write '
          'the thesis yourself instead, at no cost to their limit, see get_thesis_guide.',
          START_RESEARCH, start_thesis_research, scope='theses:research', writes=True),
]


# Offered only while config.trades_active(). Neither places an order: the first stores a trade
# for the person to review and sign in Hence, the second reads back what they did with it.
TRADE_TOOLS = [
    _tool('propose_trade', 'Prepare a trade for the person',
          'Prepare ONE perpetual order for the person to review and sign in Hence. It does NOT place the trade and you '
          'cannot place one: Hence stores it, the person opens the returned url, their own order ticket is filled in '
          'with your numbers, and they review and sign it with their own wallet (they can change anything first). Only '
          'when the person has asked for this trade or agreed to it in this conversation. Find the market with '
          'search_markets first. `size_usd` is the position\'s value, not the margin. Never say the trade was made: '
          'say it is waiting for them in Hence, give the url, and pass on every warning. Hence adds its fee to the '
          'order they sign; `hence_fee` says the most it can be.',
          PROPOSE_TRADE, propose_trade, scope='trades:propose', writes=True),
    _tool('propose_basket', 'Prepare buying a thesis for the person',
          'Prepare buying the basket of ONE thesis for the person to review and sign in Hence: the thesis, how much money '
          'to put in, and optionally which basket (perps or spot) and a leverage. It does NOT buy anything and you cannot: '
          'the person opens the returned url, the thesis page\'s own ticket is filled in with the amount, and they review '
          'the basket and sign it with their own wallet. The holdings are the thesis\'s own; you cannot change them here. '
          'Only when the person asked for this or agreed to it in this conversation. `amount_usd` is the money put in, '
          'not the positions\' value. Never say it was bought: say it is waiting for them in Hence and give the url.',
          PROPOSE_BASKET, propose_basket, scope='trades:propose', writes=True),
    _tool('get_trade_proposals', 'What happened to a prepared trade',
          'Where a trade you prepared stands: waiting, opened by the person, placed (with whether the venue\'s own '
          'record confirms the fill), dismissed, expired or withdrawn. Without a proposal_id, lists the trades you '
          'prepared for this person. Only `placed` means an order exists, and the order is what the person placed, '
          'which may differ from what you proposed. A basket is never reported as placed: Hence does not record that. '
          'For their positions, use get_my_portfolio.',
          GET_PROPOSALS, get_trade_proposals, scope='trades:propose'),
]


def listed():
    """The tools this server offers right now, in a stable order."""
    return (TOOLS + (ACCOUNT_TOOLS if config.accounts_active() else []) + (RESEARCH_TOOLS if config.research_active() else [])
            + (TRADE_TOOLS if config.trades_active() else []))


def find(name):
    return next((t for t in listed() if t['name'] == name), None)


def public_definition(tool):
    """What tools/list sends: everything but the handler and the server-side permission, plus
    the card that can draw its answer in a chat, when it has one (cards.py)."""
    from . import cards
    out = {k: v for k, v in tool.items() if k not in ('handler', 'scope')}
    meta = cards.tool_meta(tool['name'])
    if meta:
        out['_meta'] = meta
    return out
