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


def thesis(value):
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
    extra = footer(value, shown)
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


def tools(found):
    rows = [[t.get('name', ''), (t.get('title') or '')] for t in found if isinstance(t, dict)]
    return table(rows, ['tool', 'what it does'])


VIEWS = {'search_theses': theses, 'get_featured_theses': lambda v: theses(v, with_return=True), 'get_thesis': thesis,
         'search_markets': markets, 'get_my_account': account}


def show(tool, value, as_json=False):
    """The text for a tool's answer. Unknown tools (anything the server gained after this
    release) print as JSON: complete, if not pretty."""
    if as_json or tool not in VIEWS:
        return json.dumps(value, indent=2, ensure_ascii=False)
    return VIEWS[tool](value)
