"""hence: the command.

    hence theses search <words>      public theses about something
    hence theses featured            today's featured theses
    hence thesis <id or link>        one thesis in full
    hence markets <words>            what can be traded on Hence, and where
    hence asset <market id|ticker>   research on one asset: price, history, analysts, insiders, earnings, news
    hence login | logout | whoami    connect this terminal to your Hence account
    hence my theses                  your own theses, private ones included
    hence my thesis <id or link>     one of them in full
    hence portfolio                  your open positions and trading results
    hence guide                      how to write a Hence thesis yourself
    hence draft new > thesis.json    a blank thesis to fill in
    hence draft check thesis.json    what is wrong with it (nothing is saved)
    hence draft save thesis.json     save it to your Hence library as a private draft
    hence research start "<idea>"    ask Hence to research an idea (20 to 40 minutes; counts toward your daily limit)
    hence research status <run>      how a research run is going
    hence research list              what is in progress and what recently finished
    hence trade propose perp:BTC long 200   prepare a trade to review and sign in Hence (places nothing)
    hence trade propose hip4:1212:0 buy 25  the same for buying a spot token or one side of an outcome market
    hence trade basket <thesis> 500  prepare buying a thesis's basket, to review and sign in Hence (buys nothing)
    hence trade status <proposal>    what happened to it
    hence trade list                 the trades prepared from this terminal
    hence tools                      everything the server offers right now
    hence call <tool> --arg k=v      any tool, including ones newer than this release

Add --json to any of them for the exact structured answer.

Exit codes: 0 done; 1 Hence could not do it (the reason is on stderr, stdout is empty);
2 the command was not understood; 3 this needs `hence login`.
"""
import argparse
import json
import os
import re
import sys

from . import VERSION, auth, render
from .client import DEFAULT_SERVER, Client, CliError, SignInNeeded, ToolFailed, check_server
from .store import Store

UUID = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
PROPOSAL = re.compile(r'pr_[A-Za-z0-9_-]{22}(?![A-Za-z0-9_-])')


def _parser():
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS: these may be typed before or after the command, and a sub-command's unset
    # default must not overwrite a value typed before it
    common.add_argument('--json', action='store_true', default=argparse.SUPPRESS, help='print the exact structured answer')
    common.add_argument('--server', default=argparse.SUPPRESS, help='the Hence MCP server (default: $HENCE_MCP_URL or %s)' % DEFAULT_SERVER)
    p = argparse.ArgumentParser(prog='hence', description='Hence from a terminal: public theses, tradable markets, and your account.',
                                parents=[common])
    p.add_argument('--version', action='version', version='hence ' + VERSION)
    sub = p.add_subparsers(dest='command', metavar='command')

    theses = sub.add_parser('theses', help='search public theses, or list the featured ones', parents=[common])
    tsub = theses.add_subparsers(dest='what', metavar='search|featured')
    search = tsub.add_parser('search', help='public theses about something', parents=[common])
    search.add_argument('query', nargs='*')
    search.add_argument('--basket', choices=['all', 'perps', 'spot'])
    search.add_argument('--sort', choices=['relevant', 'latest', 'return_30d'])
    search.add_argument('--limit', type=int)
    featured = tsub.add_parser('featured', help="today's featured theses", parents=[common])
    featured.add_argument('--topic')
    featured.add_argument('--limit', type=int)

    thesis = sub.add_parser('thesis', help='one thesis in full', parents=[common])
    thesis.add_argument('id', help='a thesis id, or its Hence link')
    thesis.add_argument('--days', type=int, choices=[30, 90, 180, 365], help='price-history period')
    thesis.add_argument('--related', action='store_true', help='include related theses')

    markets = sub.add_parser('markets', help='what can be traded on Hence, and where', parents=[common])
    markets.add_argument('query', nargs='*')
    markets.add_argument('--kind', choices=['all', 'perp', 'spot', 'outcome'])
    markets.add_argument('--limit', type=int)

    asset = sub.add_parser('asset', help='research on one asset: price, history and, for a company, analysts, insiders, earnings, news', parents=[common])
    asset.add_argument('id', help='a market id from `hence markets` (perp:xyz:NVDA) or a ticker (NVDA)')
    asset.add_argument('--sections', help='comma-separated: quote,history,profile,analysts,insiders,earnings,news,fundamentals,analysis')
    asset.add_argument('--all', action='store_true', help='every section')

    my = sub.add_parser('my', help='your own theses (needs hence login)', parents=[common])
    msub = my.add_subparsers(dest='what', metavar='theses|thesis')
    mine = msub.add_parser('theses', help='your theses, private ones included', parents=[common])
    mine.add_argument('--which', choices=['created', 'saved', 'following', 'paper', 'archived', 'all'])
    mine.add_argument('--limit', type=int)
    mine.add_argument('--cursor')
    one = msub.add_parser('thesis', help='one of your theses in full', parents=[common])
    one.add_argument('id', help='a thesis id, or its Hence link')
    one.add_argument('--days', type=int, choices=[30, 90, 180, 365], help='price-history period')

    portfolio = sub.add_parser('portfolio', help='your open positions and trading results (needs hence login)', parents=[common])
    portfolio.add_argument('--days', type=int, help='window for the per-market results (1 to 365)')
    portfolio.add_argument('--spot', action='store_true', help='also read Robinhood Chain tokens and wallet stablecoins (slower)')

    sub.add_parser('guide', help='how to write a Hence thesis yourself', parents=[common])
    draft = sub.add_parser('draft', help='a thesis you wrote: check it, or save it as a draft (needs hence login)', parents=[common])
    dsub = draft.add_subparsers(dest='what', metavar='new|check|save')
    dsub.add_parser('new', help='print a blank thesis to fill in', parents=[common])
    for name, text in (('check', 'what is wrong with it; nothing is saved'), ('save', 'save it to your Hence library as a private draft')):
        one_draft = dsub.add_parser(name, help=text, parents=[common])
        one_draft.add_argument('file', help='the thesis as JSON, or - to read it from standard input')
        if name == 'save':
            one_draft.add_argument('--yes', action='store_true', help='do not ask before saving')

    sub.add_parser('login', help='connect this terminal to your Hence account', parents=[common])
    sub.add_parser('logout', help='disconnect this terminal', parents=[common])
    sub.add_parser('whoami', help='the Hence account this terminal is connected to', parents=[common])
    sub.add_parser('tools', help='everything the server offers right now', parents=[common])

    research = sub.add_parser('research', help='Hence research: start it, or follow it (needs hence login)', parents=[common])
    rsub = research.add_subparsers(dest='what', metavar='start|status|list')
    start = rsub.add_parser('start', help='ask Hence to research an idea; counts toward your daily limit', parents=[common])
    start.add_argument('belief', nargs='+', help='the idea, in your own words')
    start.add_argument('--guidance', help='how you want it built: constraints, emphasis, what to avoid')
    start.add_argument('--must-include', action='append', default=[], metavar='MARKET_ID', help='a market that must be in the basket (repeatable)')
    start.add_argument('--exclude', action='append', default=[], metavar='MARKET_ID', help='a market that must not be in the basket (repeatable)')
    start.add_argument('--basket', choices=['perps', 'spot'], help='where to build it; leave out and Hence picks')
    start.add_argument('--wait', action='store_true', help='stay and report when it finishes (checks once a minute)')
    start.add_argument('--yes', action='store_true', help='do not ask before spending one of your daily research runs')
    status = rsub.add_parser('status', help='how a research run is going', parents=[common])
    status.add_argument('run', help='the run id `hence research start` printed')
    status.add_argument('--wait', action='store_true', help='stay and report when it finishes (checks once a minute)')
    rsub.add_parser('list', help='what is in progress and what recently finished', parents=[common])

    trade = sub.add_parser('trade', help='prepare a trade to review and sign in Hence; it places nothing (needs hence login)', parents=[common])
    xsub = trade.add_subparsers(dest='what', metavar='propose|basket|status|list')
    tbasket = xsub.add_parser('basket', help="prepare buying a thesis's basket; you review and sign it in Hence", parents=[common])
    tbasket.add_argument('thesis', help='a thesis id, or its Hence link')
    tbasket.add_argument('amount', type=float, help='the money put in, in US dollars')
    tbasket.add_argument('--basket', choices=['perps', 'spot'], help="which of the thesis's baskets; leave out for its own")
    tbasket.add_argument('--leverage', type=int, help='perpetual baskets only; leave out for none')
    tbasket.add_argument('--note', help='why, in a sentence or two; shown in Hence beside the trade')
    tbasket.add_argument('--valid-hours', type=int, help='how long you have to act on it (default 24, at most 72)')
    tbasket.add_argument('--yes', action='store_true', help='do not ask before sending it to your Hence account')
    propose = xsub.add_parser('propose', help='prepare one order (a perpetual, a spot token or one side of an outcome market); you review and sign it in Hence',
                              parents=[common])
    propose.add_argument('market', help='a market id from `hence markets`: perp:BTC, perp:xyz:NVDA, spot:4663:0x..., spot:solana:<mint>, hip4:1212:0')
    propose.add_argument('side', choices=['long', 'short', 'buy'], help='long or short for a perpetual; buy for a spot token or an outcome contract')
    propose.add_argument('size', type=float, help="a perpetual: the position's value in US dollars (not the margin). A purchase: the dollars to spend")
    propose.add_argument('--leverage', type=int, help="perpetuals only; leave out to keep your own setting for the market")
    propose.add_argument('--limit', type=float, metavar='PRICE', help='perpetuals only: make it a limit order at this price')
    propose.add_argument('--note', help='why, in a sentence or two; shown in Hence beside the trade')
    propose.add_argument('--valid-hours', type=int, help='how long you have to act on it (default 24, at most 72)')
    propose.add_argument('--yes', action='store_true', help='do not ask before sending it to your Hence account')
    tstatus = xsub.add_parser('status', help='what happened to a prepared trade', parents=[common])
    tstatus.add_argument('proposal', help='the proposal id `hence trade propose` printed, or its Hence link')
    xsub.add_parser('list', help='the trades prepared from this terminal', parents=[common])

    call = sub.add_parser('call', help='call any tool by name', parents=[common])
    call.add_argument('tool')
    call.add_argument('--arg', action='append', default=[], metavar='KEY=VALUE', help='one argument; the value is read as JSON when it is JSON')
    call.add_argument('--args', dest='json_args', metavar='JSON', help='all arguments as one JSON object')
    return p


def _arguments(ns):
    """(tool name, arguments) for the command typed. Options left out are not sent, so the
    server's own defaults apply."""
    def some(**kw):
        # by identity, not equality: 0 == False in Python, so `--limit 0` was dropped as "not given" and the
        # server's default answered instead of its refusal. A 0 typed is a 0 sent.
        return {k: v for k, v in kw.items() if v is not None and v is not False and v != ''}
    if ns.command == 'theses':
        if ns.what == 'featured':
            return 'get_featured_theses', some(topic=ns.topic, limit=ns.limit)
        if ns.what == 'search':
            return 'search_theses', some(query=' '.join(ns.query), basket=ns.basket, sort=ns.sort, limit=ns.limit)
        raise CliError('Say `hence theses search <words>` or `hence theses featured`.')
    if ns.command == 'thesis':
        found = UUID.search(ns.id)
        if not found:
            raise CliError('"%s" is not a thesis id or a Hence thesis link.' % ns.id)
        return 'get_thesis', some(thesis_id=found.group(0).lower(), period_days=ns.days, include_related=ns.related)
    if ns.command == 'markets':
        return 'search_markets', some(query=' '.join(ns.query), kind=ns.kind, limit=ns.limit)
    if ns.command == 'asset':
        every = ['quote', 'history', 'profile', 'analysts', 'insiders', 'earnings', 'news', 'fundamentals', 'analysis']
        sections = every if ns.all else [s.strip() for s in (ns.sections or '').split(',') if s.strip()]
        return 'get_asset', some(id=ns.id, sections=sections or None)
    if ns.command == 'whoami':
        return 'get_my_account', {}
    if ns.command == 'my':
        if ns.what == 'theses':
            return 'list_my_theses', some(which=ns.which, limit=ns.limit, cursor=ns.cursor)
        if ns.what == 'thesis':
            found = UUID.search(ns.id)
            if not found:
                raise CliError('"%s" is not a thesis id or a Hence thesis link.' % ns.id)
            return 'get_my_thesis', some(thesis_id=found.group(0).lower(), period_days=ns.days)
        raise CliError('Say `hence my theses` or `hence my thesis <id>`.')
    if ns.command == 'portfolio':
        return 'get_my_portfolio', some(history_days=ns.days, include_spot=ns.spot)
    if ns.command == 'guide':
        return 'get_thesis_guide', {}
    if ns.command == 'call':
        args = {}
        if ns.json_args:
            try:
                args = json.loads(ns.json_args)
            except ValueError:
                raise CliError('--args is not valid JSON.')
            if not isinstance(args, dict):
                raise CliError('--args must be a JSON object.')
        for pair in ns.arg:
            key, eq, raw = pair.partition('=')
            if not eq or not key:
                raise CliError('--arg takes KEY=VALUE, got "%s".' % pair)
            try:
                args[key] = json.loads(raw)
            except ValueError:
                args[key] = raw
        return ns.tool, args
    raise CliError('Unknown command.')


TEMPLATE = {
    'format': 'hence-thesis/1',
    'title': 'The claim, in one line',
    'summary': 'If X, then Y.',
    'mechanism': 'How the claim becomes a price move, and why the market has not priced it.',
    'why_now': 'The dated events that will show whether it is right.',
    'counter_case': 'The strongest argument against it.',
    'invalidation': 'What would prove it wrong.',
    'horizon_days': 180,
    'topic': 'AI & compute',
    'cash_weight_pct': 20,
    'holdings': [
        {'market_id': 'perp:BTC', 'side': 'long', 'weight_pct': 80, 'role': 'core', 'reason': 'What it is, its role, and why this side.'},
    ],
    'sources': [{'title': 'What you read', 'url': 'https://example.com/the-page', 'publisher': 'Who published it', 'published_at': '2026-01-31'}],
    'checkpoints': [{'by': '2027-01-31', 'observable': 'What to look for', 'direction': 'supports'}],
}


def _read_thesis(path, stdin):
    try:
        if path == '-':
            raw = stdin.read()
        else:
            with open(path, encoding='utf-8') as f:
                raw = f.read()
    except OSError as e:
        raise CliError('"%s" could not be read (%s).' % (path, e.strerror or e.__class__.__name__))
    try:
        thesis = json.loads(raw)
    except ValueError as e:
        raise CliError('"%s" is not valid JSON (%s).' % (path, e))
    if not isinstance(thesis, dict):
        raise CliError('"%s" must hold one JSON object: the thesis.' % path)
    return thesis


def _call_signed_in(server, tool, args, store, sender):
    """Call a tool, signing in only when the server asks. A public tool never sees the
    credential; a tool that needs the account gets it, renewed once if it has expired."""
    kw = {'sender': sender} if sender else {}
    try:
        return Client(server, **kw).call(tool, args)
    except SignInNeeded as first:
        if first.missing_permission:
            raise SignInNeeded('%s Run `hence login` again to allow it.' % first, scope=first.scope, missing_permission=True)
        token = store.access_token(server) or auth.refresh(server, store, **kw)
        if not token:
            raise SignInNeeded('This needs your Hence account. Run `hence login` first.', scope=first.scope)
        try:
            return Client(server, token=token, **kw).call(tool, args)
        except SignInNeeded as second:
            if second.missing_permission:
                raise SignInNeeded('%s Run `hence login` again to allow it.' % second, scope=second.scope, missing_permission=True)
            # the stored token was refused: one renewal, then it is the person's turn
            token = auth.refresh(server, store, **kw)
            if not token:
                store.forget_tokens(server)
                raise SignInNeeded('This terminal is no longer connected to Hence. Run `hence login`.', scope=second.scope)
            return Client(server, token=token, **kw).call(tool, args)


WAIT_EVERY_S = 60
WAIT_LIMIT_S = 60 * 60          # research takes 20 to 40 minutes; past an hour, stop watching and say so


def _follow(server, run_id, store, sender, err, sleep, clock):
    """Watch a run until Hence says it finished, printing what it is doing when that changes.
    A failed check does not end the watch: the run is still going whether or not we can see it."""
    started, last = clock(), None
    while True:
        try:
            run = _call_signed_in(server, 'get_thesis_research', {'run_id': run_id}, store, sender)
        except SignInNeeded:
            raise
        except CliError as e:
            run = None
            print('hence: could not check just now (%s). Still watching.' % e, file=err)
        if run and run.get('finished') is True:
            return run
        now = (run or {}).get('now') or (run or {}).get('status')
        if now and now != last:
            print('  %s' % now, file=err)
            last = now
        if clock() - started > WAIT_LIMIT_S:
            raise CliError('Still running after an hour. It carries on without this terminal: check later with `hence research status %s`.' % run_id)
        sleep(WAIT_EVERY_S)


def run(argv, out=None, err=None, store=None, sender=None, open_browser=None, stdin=None, confirm=None, sleep=None, clock=None):
    """The command, with its edges injectable for tests. Returns the exit code."""
    out, err = out or sys.stdout, err or sys.stderr
    parser = _parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    if not ns.command:
        parser.print_help(out)
        return 0
    ns.json, ns.server = getattr(ns, 'json', False), getattr(ns, 'server', None)
    store = store or Store()
    kw = {'sender': sender} if sender else {}
    try:
        server = check_server(ns.server or os.environ.get('HENCE_MCP_URL') or DEFAULT_SERVER)
        if ns.command == 'login':
            extra = {'open_browser': open_browser} if open_browser else {}
            scope = auth.login(server, store, say=lambda line: print(line, file=err), **kw, **extra)
            print('Connected.%s Disconnect any time with `hence logout`, or in Hence under Settings.' % (' This terminal may: %s.' % scope if scope else ''), file=out)
            return 0
        if ns.command == 'logout':
            had = bool(store.get(server).get('access_token') or store.get(server).get('refresh_token'))
            confirmed = auth.logout(server, store, **kw)
            if not had:
                print('This terminal was not connected.', file=out)
            elif confirmed:
                print('Disconnected. Hence has closed the connection.', file=out)
            else:
                print('The sign-in was removed from this terminal, but Hence did not confirm. To be sure, disconnect it in Hence under Settings.', file=out)
            return 0
        if ns.command == 'tools':
            found = Client(server, **kw).tools()
            print(json.dumps(found, indent=2, ensure_ascii=False) if ns.json else render.tools(found), file=out)
            return 0
        if ns.command == 'research':
            import time as _time
            sleep, clock = sleep or _time.sleep, clock or _time.time
            if ns.what == 'list':
                print(render.show('get_thesis_research', _call_signed_in(server, 'get_thesis_research', {}, store, sender), as_json=ns.json), file=out)
                return 0
            if ns.what == 'status':
                found = UUID.search(ns.run)
                if not found:
                    raise CliError('"%s" is not a research run id.' % ns.run)
                run_id = found.group(0).lower()
                state = _follow(server, run_id, store, sender, err, sleep, clock) if ns.wait else \
                    _call_signed_in(server, 'get_thesis_research', {'run_id': run_id}, store, sender)
                print(render.show('get_thesis_research', state, as_json=ns.json), file=out)
                return 0
            if ns.what != 'start':
                raise CliError('Say `hence research start "<idea>"`, `hence research status <run>` or `hence research list`.')
            request = {k: v for k, v in (('belief', ' '.join(ns.belief)), ('guidance', ns.guidance), ('must_include', ns.must_include),
                                         ('exclude', ns.exclude), ('basket', ns.basket)) if v}
            if not ns.yes:
                # a run spends one of the person's daily research runs: they say yes first
                print('Hence will research: %s' % request['belief'], file=err)
                ask = confirm or (lambda: (stdin or sys.stdin).isatty() and input(
                    'This takes 20 to 40 minutes and uses one of your daily research runs. Start it? [y/N] ').strip().lower() in ('y', 'yes'))
                if not ask():
                    raise CliError('Not started. To start without being asked, add --yes.')
            started = _call_signed_in(server, 'start_thesis_research', request, store, sender)
            if started.get('started') is not True:
                print(render.show('start_thesis_research', started, as_json=ns.json), file=out)
                return 1
            if not ns.wait:
                print(render.show('start_thesis_research', started, as_json=ns.json), file=out)
                return 0
            print(render.show('start_thesis_research', started), file=err)
            state = _follow(server, started['run']['run_id'], store, sender, err, sleep, clock)
            print(render.show('get_thesis_research', state, as_json=ns.json), file=out)
            return 0 if state.get('thesis_id') else 1
        if ns.command == 'trade':
            if ns.what == 'list':
                print(render.show('get_trade_proposals', _call_signed_in(server, 'get_trade_proposals', {}, store, sender), as_json=ns.json), file=out)
                return 0
            if ns.what == 'status':
                found = PROPOSAL.search(ns.proposal)
                if not found:
                    raise CliError('"%s" is not a proposal id or a Hence proposal link.' % ns.proposal)
                state = _call_signed_in(server, 'get_trade_proposals', {'proposal_id': found.group(0)}, store, sender)
                print(render.show('get_trade_proposals', state, as_json=ns.json), file=out)
                return 0
            if ns.what == 'basket':
                found = UUID.search(ns.thesis)
                if not found:
                    raise CliError('"%s" is not a thesis id or a Hence thesis link.' % ns.thesis)
                request = {'thesis_id': found.group(0).lower(), 'amount_usd': int(ns.amount) if ns.amount == int(ns.amount) else ns.amount}
                for key, value in (('basket', ns.basket), ('leverage', ns.leverage), ('note', ns.note), ('valid_for_hours', ns.valid_hours)):
                    if value not in (None, ''):
                        request[key] = value
                if not ns.yes:
                    print('Prepare for review in Hence: buy the basket of thesis %s with $%s%s' % (
                        request['thesis_id'], ('%g' % ns.amount), (' at %dx' % ns.leverage) if ns.leverage is not None else ''), file=err)
                    ask = confirm or (lambda: (stdin or sys.stdin).isatty() and input(
                        'This buys nothing: it waits in Hence for you to review and sign. Send it? [y/N] ').strip().lower() in ('y', 'yes'))
                    if not ask():
                        raise CliError('Not sent. To send without being asked, add --yes.')
                answer = _call_signed_in(server, 'propose_basket', request, store, sender)
                print(render.show('propose_basket', answer, as_json=ns.json), file=out)
                return 0 if answer.get('proposed') is True else 1
            if ns.what != 'propose':
                raise CliError('Say `hence trade propose <market> <long|short> <size>`, `hence trade basket <thesis> <amount>`, '
                               '`hence trade status <proposal>` or `hence trade list`.')
            # "buy" is this terminal's word for a purchase; the server's word for the same thing is "long". What a
            # purchase cannot have (a short, a leverage, a limit) is sent as typed and refused by the server by name.
            purchase = ns.side == 'buy'
            request = {'market_id': ns.market, 'side': 'long' if purchase else ns.side, 'size_usd': int(ns.size) if ns.size == int(ns.size) else ns.size}
            if ns.leverage is not None:
                request['leverage'] = ns.leverage
            if ns.limit is not None:
                request.update(order_type='limit', limit_price=ns.limit)
            if ns.note:
                request['note'] = ns.note
            if ns.valid_hours is not None:
                request['valid_for_hours'] = ns.valid_hours
            if not ns.yes:
                # it lands in the person's Hence account: they say yes first. Nothing is placed either way.
                print('Prepare for review in Hence: %s %s, $%s%s%s' % (
                    ns.side, ns.market, ('%g' % ns.size), (' at %dx' % ns.leverage) if ns.leverage is not None else '',
                    (', limit %g' % ns.limit) if ns.limit is not None else '' if purchase else ', market order'), file=err)
                ask = confirm or (lambda: (stdin or sys.stdin).isatty() and input(
                    'This places nothing: it waits in Hence for you to review and sign. Send it? [y/N] ').strip().lower() in ('y', 'yes'))
                if not ask():
                    raise CliError('Not sent. To send without being asked, add --yes.')
            answer = _call_signed_in(server, 'propose_trade', request, store, sender)
            print(render.show('propose_trade', answer, as_json=ns.json), file=out)
            return 0 if answer.get('proposed') is True else 1
        if ns.command == 'draft':
            if ns.what == 'new':
                print(json.dumps(TEMPLATE, indent=2), file=out)
                return 0
            if ns.what not in ('check', 'save'):
                raise CliError('Say `hence draft new`, `hence draft check <file>` or `hence draft save <file>`.')
            thesis = _read_thesis(ns.file, stdin or sys.stdin)
            verdict = _call_signed_in(server, 'check_thesis', {'thesis': thesis}, store, sender)
            if ns.what == 'check' or verdict.get('ok') is not True:
                print(render.show('check_thesis', verdict, as_json=ns.json), file=out)
                return 0 if verdict.get('ok') is True else 1         # a thesis with problems is a failed check
            if not getattr(ns, 'yes', False):
                # the person sees exactly what will be saved, and says yes, before anything is
                print(render.show('check_thesis', verdict), file=err)
                ask = confirm or (lambda: (stdin or sys.stdin).isatty() and input('Save this as a private draft in your Hence library? [y/N] ').strip().lower() in ('y', 'yes'))
                if not ask():
                    raise CliError('Not saved. To save without being asked, add --yes.')
            saved = _call_signed_in(server, 'save_thesis_draft', {'thesis': thesis}, store, sender)
            print(render.show('save_thesis_draft', saved, as_json=ns.json), file=out)
            return 0 if saved.get('saved') is True else 1
        tool, args = _arguments(ns)
        try:
            value = _call_signed_in(server, tool, args, store, sender)
        except ToolFailed as e:
            if ns.command == 'whoami' and 'Unknown tool' in str(e):
                raise auth.SignInUnavailable('This Hence server is not accepting sign-ins yet. Tools that do not need an account still work.')
            raise
        print(render.show(tool, value, as_json=ns.json), file=out)
        return 0
    except CliError as e:
        print('hence: %s' % e, file=err)
        return e.exit_code
    except KeyboardInterrupt:
        print('hence: stopped.', file=err)
        return 130


def main():
    sys.exit(run(sys.argv[1:]))


if __name__ == '__main__':
    main()
