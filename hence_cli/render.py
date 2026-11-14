"""Tool answers as text a person can read in a terminal.

Two rules. A figure that was not read (null) is shown as "n/a", never as 0 or a blank that
could be taken for one. And nothing the server said is dropped: any top-level field a view
does not lay out itself is printed underneath it, so a note such as "one venue could not be
read" or "results are hypothetical" always reaches the person.
"""
import json

NA = 'n/a'


def pct(v, signed=True):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return NA
    return ('%+.2f%%' if signed else '%.2f%%') % v


def money(v):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return NA
    for limit, suffix in ((1e9, 'B'), (1e6, 'M'), (1e3, 'K')):
        if abs(v) >= limit:
            return '$%.2f%s' % (v / limit, suffix)
    return '$%.2f' % v


def price(v):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return NA
    return ('%.2f' % v) if abs(v) >= 1 else ('%.6g' % v)


def count(v):
    """A whole number of things (trades, wins). The app stores some of them as floats."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return NA
    return '%d' % v if float(v).is_integer() else '%g' % v


def text(v):
    return v if isinstance(v, str) and v else NA


def table(rows, headers):
    if not rows:
        return ''
    widths = [max(len(str(c)) for c in col) for col in zip(headers, *rows)]
    line = lambda cells: '  '.join(str(c).ljust(w) for c, w in zip(cells, widths)).rstrip()
    return '\n'.join([line(headers), line(['-' * w for w in widths])] + [line(r) for r in rows])


def _holding(h):
    if not isinstance(h, dict):
        return ''
    weight = h.get('weight_pct')
    return ' '.join(x for x in (text(h.get('symbol')), text(h.get('side')), ('%g%%' % weight) if isinstance(weight, (int, float)) else NA) if x)


def footer(value, shown):
    """Every top-level field the view did not lay out, so nothing is silently dropped."""
    out = []
    for key, v in value.items():
        if key in shown or v is None or v == [] or v == {}:
            continue
        label = key.replace('_', ' ')
        out.append('%s: %s' % (label, v if isinstance(v, (str, int, float)) and not isinstance(v, bool) else json.dumps(v, ensure_ascii=False)))
    return '\n'.join(out)


def _thesis_line(t, with_return=False):
    lines = ['%s  (%s, %s)' % (text(t.get('title')), text(t.get('author')), text(t.get('topic')))]
    if with_return:
        span = '%s to %s' % (t.get('return_1m_from'), t.get('return_1m_to')) if t.get('return_1m_from') and t.get('return_1m_to') else 'period not read'
        lines.append('  1 month: %s (%s)   basket: %s' % (pct(t.get('return_1m_pct')), span, text(t.get('basket'))))
    if isinstance(t.get('summary'), str) and t['summary']:
        lines.append('  ' + t['summary'])
    holdings = [_holding(h) for h in t.get('top_holdings') or []]
    count = t.get('holdings_count')
    if holdings:
        lines.append('  holds: %s%s' % (', '.join(h for h in holdings if h), (' (of %d)' % count) if isinstance(count, int) and count > len(holdings) else ''))
    lines.append('  ' + text(t.get('url')))
    return '\n'.join(lines)


def theses(value, with_return=False):
    rows = value.get('theses')
    if not isinstance(rows, list):
        return json.dumps(value, indent=2, ensure_ascii=False)
    body = '\n\n'.join(_thesis_line(t, with_return) for t in rows if isinstance(t, dict)) or 'No theses matched.'
    extra = footer(value, {'theses', 'next_cursor'})
    return body + ('\n\n' + extra if extra else '')


def thesis(value, also_shown=()):
    out = ['%s  (%s, %s)' % (text(value.get('title')), text(value.get('author')), text(value.get('topic')))]
    for label, key in (('Summary', 'summary'), ('Why now', 'why_now'), ('Counter-case', 'counter_case'), ('Wrong if', 'invalidation')):
        if isinstance(value.get(key), str) and value[key]:
            out.append('\n%s: %s' % (label, value[key]))
    holdings = value.get('holdings')
    if isinstance(holdings, list) and holdings:
        rows = [[text(h.get('symbol')), text(h.get('side')), ('%g%%' % h['weight_pct']) if isinstance(h.get('weight_pct'), (int, float)) else NA,
                 text(h.get('venue')), text(h.get('role'))] for h in holdings if isinstance(h, dict)]
        out.append('\n' + table(rows, ['holding', 'side', 'weight', 'venue', 'role']))
    perf = value.get('performance')
    if isinstance(perf, dict):
        out.append('\nPrice history, %s days (%s to %s): %s   %s: %s   worst drop: %s' % (
            perf.get('period_days', NA), text(perf.get('from')), text(perf.get('to')), pct(perf.get('return_pct')),
            text(perf.get('benchmark')), pct(perf.get('benchmark_return_pct')), pct(perf.get('max_drawdown_pct'))))
        if perf.get('complete') is not True:
            left = perf.get('holdings_left_out')
            out.append('  incomplete: %s holdings have no price history and are left out' % (left if isinstance(left, int) else 'some'))
        latest = perf.get('latest')
        if isinstance(latest, dict):
            out.append('  latest: %s as of %s%s' % (pct(latest.get('return_pct')), text(latest.get('at')), '' if latest.get('live') else ' (not live)'))
    elif 'performance' in value:
        out.append('\nPrice history: %s' % NA)
    checks = value.get('checkpoints')
    if isinstance(checks, list) and checks:
        out.append('\nCheckpoints:')
        out += ['  by %s: %s (%s)' % (text(c.get('by')), text(c.get('observable')), text(c.get('direction'))) for c in checks if isinstance(c, dict)]
    out.append('\n' + text(value.get('url')))
    shown = {'title', 'author', 'topic', 'summary', 'why_now', 'counter_case', 'invalidation', 'holdings', 'performance', 'checkpoints', 'url',
             'thesis_id', 'mechanism', 'sources', 'upkeep', 'other_baskets', 'paper_holdings', 'version_recorded_at', 'created_at', 'related'}
    extra = footer(value, shown | set(also_shown))
    return '\n'.join(out) + ('\n' + extra if extra else '')


def markets(value):
    rows = value.get('markets')
    if not isinstance(rows, list):
        return json.dumps(value, indent=2, ensure_ascii=False)
    cells = [[text(m.get('symbol')), text(m.get('kind')), text(m.get('venue')), price(m.get('price')), pct(m.get('change_24h_pct')),
              pct(m.get('funding_apr_pct')), money(m.get('open_interest_usd')), money(m.get('volume_24h_usd')), text(m.get('url'))]
             for m in rows if isinstance(m, dict)]
    body = table(cells, ['symbol', 'kind', 'venue', 'price', '24h', 'funding/yr', 'open interest', 'volume 24h', 'open in Hence']) or 'No markets matched.'
    extra = footer(value, {'markets'})
    return body + ('\n\n' + extra if extra else '')


def account(value):
    """`get_my_account`. A username of null is a fact the server read (the account has not
    claimed one); the server says so in `note`, which is printed as it came."""
    name = value.get('username')
    out = ['Signed in as @%s' % name if isinstance(name, str) and name else 'Signed in to a Hence account']
    if isinstance(value.get('profile_url'), str) and value['profile_url']:
        out.append(value['profile_url'])
    if isinstance(value.get('connected_agent'), str) and value['connected_agent']:
        out.append('Connected as: ' + value['connected_agent'])
    allowed = value.get('allowed')
    if isinstance(allowed, list) and allowed:
        out.append('This connection may: ' + ', '.join(str(s) for s in allowed))
    extra = footer(value, {'username', 'profile_url', 'connected_agent', 'allowed'})
    return '\n'.join(out) + ('\n' + extra if extra else '')


def my_theses(value):
    rows = value.get('theses')
    if not isinstance(rows, list):
        return json.dumps(value, indent=2, ensure_ascii=False)
    lines = []
    for t in rows:
        if not isinstance(t, dict):
            continue
        marks = [text(t.get('visibility')), text(t.get('status'))] + (['research in progress'] if t.get('research_in_progress') else []) \
            + ([] if t.get('yours') else ['not yours'])
        lines.append('%s  [%s]\n  %s' % (text(t.get('title')), ', '.join(marks), text(t.get('url'))))
    body = '\n\n'.join(lines) or 'No theses in this list.'
    if value.get('next_cursor'):
        body += '\n\nMore: add --cursor %s' % value['next_cursor']
    extra = footer(value, {'theses', 'next_cursor'})
    return body + ('\n\n' + extra if extra else '')


def my_thesis(value):
    head = '[%s, %s%s]' % (text(value.get('visibility')), text(value.get('status')), '' if value.get('yours') else ', not yours')
    return head + '\n' + thesis(value, also_shown=('yours', 'visibility', 'status', 'saved', 'archived', 'research_in_progress', 'versions'))


def portfolio(value):
    out = []
    held = value.get('open_positions')
    state = value.get('positions') if isinstance(value.get('positions'), dict) else {}
    if held is None:
        out.append('Open perpetual positions: %s (they could not be read)' % NA)
    elif not held:
        out.append('Open perpetual positions: none')
    else:
        rows = [[text(p.get('coin')), text(p.get('side')), ('%g' % p['size']) if isinstance(p.get('size'), (int, float)) else NA,
                 price(p.get('entry_price')), money(p.get('value_usd')), money(p.get('unrealized_pnl_usd')),
                 ('%gx' % p['leverage']) if isinstance(p.get('leverage'), (int, float)) else NA, text(p.get('venue')), text(p.get('wallet'))]
                for p in held if isinstance(p, dict)]
        out.append('Open perpetual positions\n' + table(rows, ['market', 'side', 'size', 'entry', 'value', 'unrealized', 'leverage', 'venue', 'wallet']))
    if held is not None and state.get('complete') is not True:
        missing = ['%s on %s' % (text(u.get('wallet')), text(u.get('venue'))) for u in state.get('could_not_read') or [] if isinstance(u, dict)]
        out.append('  not complete: ' + ('could not read ' + ', '.join(missing) if missing else 'some wallets were not read'))
    results = value.get('trading_results')
    if not isinstance(results, dict):
        out.append('\nTrading results: %s (they could not be read)' % NA)
    elif results.get('has_history') is not True:
        out.append('\nTrading results: nothing recorded yet')
    else:
        windows = results.get('windows') if isinstance(results.get('windows'), dict) else {}
        rows = [[('%s days' % w) if w != 'all' else 'all time', money(v.get('realized')), money(v.get('fees')), money(v.get('volume')),
                 count(v.get('trades')), count(v.get('wins')), count(v.get('losses'))]
                for w, v in windows.items() if isinstance(v, dict)]
        out.append('\nRealized trading results (recorded fills)\n' + table(rows, ['window', 'realized', 'fees', 'volume', 'trades', 'wins', 'losses']))
    recorded = value.get('last_recorded_value')
    if isinstance(recorded, dict):
        out.append('\nLast account value Hence recorded: %s (recorded at %s, not the value now)' % (money(recorded.get('usd')), recorded.get('recorded_at_ms', NA)))
    missing = [n for n in value.get('not_included') or [] if isinstance(n, str)]
    if missing:
        out.append('\nNot included (this is not the whole portfolio):\n' + '\n'.join('  - ' + n for n in missing))
    extra = footer(value, {'open_positions', 'positions', 'trading_results', 'last_recorded_value', 'not_included', 'trading_results_days', 'as_of_ms'})
    return '\n'.join(out) + ('\n\n' + extra if extra else '')


def check(value):
    """`check_thesis` and `save_thesis_draft`: Hence's verdict on a thesis the agent wrote."""
    out = []
    problems = [p for p in value.get('problems') or [] if isinstance(p, dict)]
    if problems:
        out.append('%d problem%s. Nothing was saved.' % (len(problems), '' if len(problems) == 1 else 's'))
        out += ['  %s%s' % ((p.get('field') + ': ') if p.get('field') else '', text(p.get('problem'))) for p in problems]
    preview = value.get('thesis') if isinstance(value.get('thesis'), dict) else None
    if preview and not problems:
        out.append('%s  (%s, %s days, %s)' % (text(preview.get('title')), text(preview.get('topic')), preview.get('horizon_days', NA), text(preview.get('basket'))))
        rows = [[text(h.get('symbol')), text(h.get('name')), text(h.get('side')),
                 ('%g%%' % h['weight_pct']) if isinstance(h.get('weight_pct'), (int, float)) else NA, text(h.get('venue')), text(h.get('role'))]
                for h in preview.get('holdings') or [] if isinstance(h, dict)]
        out.append(table(rows, ['holding', 'name', 'side', 'weight', 'venue', 'role']))
        cash = preview.get('cash_weight_pct')
        out.append('cash: %s' % (('%g%%' % cash) if isinstance(cash, (int, float)) else NA))
    warnings = [w for w in value.get('warnings') or [] if isinstance(w, str)]
    if warnings:
        out.append('\nWorth knowing:')
        out += ['  - ' + w for w in warnings]
    if value.get('saved') is True:
        out.append('\n%s as a private draft: %s' % ('Updated' if value.get('already_saved') else 'Saved', text(value.get('url'))))
    extra = footer(value, {'ok', 'saved', 'problems', 'warnings', 'thesis', 'saved_as', 'draft_id', 'url', 'already_saved'})
    return '\n'.join(out) + ('\n\n' + extra if extra else '')


def research(value):
    """`start_thesis_research` and `get_thesis_research`."""
    if isinstance(value.get('in_progress'), list):
        out = []
        rows = [[text(r.get('idea')), text(r.get('stage') or r.get('status')), text(r.get('run_id')), text(r.get('url'))] for r in value['in_progress']]
        out.append('In progress\n' + table(rows, ['idea', 'stage', 'run', 'open in Hence']) if rows else 'Nothing in progress.')
        done = [[text(r.get('idea')), text(r.get('result')), text(r.get('at')), text(r.get('url'))] for r in value.get('recently_finished') or []]
        if done:
            out.append('\nRecently finished\n' + table(done, ['idea', 'result', 'when', 'open in Hence']))
        return '\n'.join(out)
    problems = [p for p in value.get('problems') or [] if isinstance(p, dict)]
    if problems:
        return '\n'.join(['%d problem%s. Nothing was started.' % (len(problems), '' if len(problems) == 1 else 's')]
                         + ['  %s%s' % ((p.get('field') + ': ') if p.get('field') else '', text(p.get('problem'))) for p in problems])
    run = value.get('run') if isinstance(value.get('run'), dict) else value
    out = ['%s  [%s]' % (text(run.get('idea')), text(run.get('status')))]
    if run.get('now'):
        out.append('  now: ' + run['now'])
    if run.get('outcome'):
        out.append('  ' + run['outcome'])
    if run.get('note'):
        out.append('  ' + run['note'])
    out.append('  thesis: %s' % (run['url'] if run.get('url') else ('none' if run.get('finished') else 'not ready yet')))
    out.append('  run: %s' % text(run.get('run_id')))
    out.append('  progress: %s' % text(run.get('progress_url')))
    nxt = value.get('next') or run.get('next')
    if isinstance(nxt, str):
        out.append('\n' + nxt)
    return '\n'.join(out)


def _exact(v):
    """A trade's size to the cent: never abbreviated, a person is about to sign for it."""
    return ('$' + format(v, ',.2f')) if isinstance(v, (int, float)) and not isinstance(v, bool) else NA


def _proposal(p):
    head = '%s %s  %s%s  %s  [%s]' % (
        'Short' if p.get('side') == 'short' else 'Long', text(p.get('market') or p.get('market_id')), _exact(p.get('size_usd')),
        (' at %sx' % p['leverage']) if isinstance(p.get('leverage'), int) else '',
        ('limit ' + price(p.get('limit_price'))) if p.get('order_type') == 'limit' else 'market order', text(p.get('status')))
    out = [head]
    if p.get('note'):
        out.append('  note: ' + p['note'])
    order = p.get('order') if isinstance(p.get('order'), dict) else None
    if order:
        out.append('  order: %s, %s, %s' % (text(order.get('state')), _exact(order.get('size_usd')),
                                           'confirmed by the venue' if order.get('confirmed_by_venue') is True else 'not yet confirmed by the venue'))
    else:
        out.append('  Not placed.')
    if p.get('status') in ('waiting', 'opened'):
        out.append('  review and sign in Hence: %s' % text(p.get('url')))
        out.append('  proposal: %s   expires: %s' % (text(p.get('proposal_id')), text(p.get('expires_at'))))
    else:
        # it can no longer be opened: the link is only where it is shown, not something to act on
        out.append('  proposal: %s   in Hence: %s' % (text(p.get('proposal_id')), text(p.get('url'))))
    if p.get('what_now'):
        out.append('  ' + p['what_now'])
    return '\n'.join(out)


def trade(value):
    """`propose_trade` and `get_trade_proposals`. A trade is shown as placed only when the
    answer carries an order; everything else says "Not placed"."""
    if isinstance(value.get('proposals'), list):
        rows = [p for p in value['proposals'] if isinstance(p, dict)]
        return '\n\n'.join(_proposal(p) for p in rows) if rows else 'No trades have been prepared from this terminal.'
    problems = [p for p in value.get('problems') or [] if isinstance(p, dict)]
    if problems:
        return '\n'.join(['%d problem%s. Nothing was prepared.' % (len(problems), '' if len(problems) == 1 else 's')]
                         + ['  %s%s' % ((p.get('field') + ': ') if p.get('field') else '', text(p.get('problem'))) for p in problems])
    p = value.get('proposal') if isinstance(value.get('proposal'), dict) else value
    out = [_proposal(p)]
    warnings = [w for w in value.get('warnings') or [] if isinstance(w, str)]
    if warnings:
        out.append('\nWorth knowing:')
        out += ['  - ' + w for w in warnings]
    fee = value.get('hence_fee') if isinstance(value.get('hence_fee'), dict) else None
    if fee and isinstance(fee.get('note'), str):
        out.append('\n' + fee['note'])
    return '\n'.join(out)


def guide(value):
    return value.get('guide') if isinstance(value.get('guide'), str) else json.dumps(value, indent=2, ensure_ascii=False)


def tools(found):
    rows = [[t.get('name', ''), (t.get('title') or '')] for t in found if isinstance(t, dict)]
    return table(rows, ['tool', 'what it does'])


VIEWS = {'search_theses': theses, 'get_featured_theses': lambda v: theses(v, with_return=True), 'get_thesis': thesis,
         'search_markets': markets, 'get_my_account': account, 'list_my_theses': my_theses, 'get_my_thesis': my_thesis,
         'get_my_portfolio': portfolio, 'get_thesis_guide': guide, 'check_thesis': check, 'save_thesis_draft': check,
         'start_thesis_research': research, 'get_thesis_research': research, 'propose_trade': trade, 'get_trade_proposals': trade}


def show(tool, value, as_json=False):
    """The text for a tool's answer. Unknown tools (anything the server gained after this
    release) print as JSON: complete, if not pretty."""
    if as_json or tool not in VIEWS:
        return json.dumps(value, indent=2, ensure_ascii=False)
    return VIEWS[tool](value)
