"""hence: the command.

    hence theses search <words>      public theses about something
    hence theses featured            today's featured theses
    hence thesis <id or link>        one thesis in full
    hence markets <words>            what can be traded on Hence, and where
    hence login | logout | whoami    connect this terminal to your Hence account
    hence my theses                  your own theses, private ones included
    hence my thesis <id or link>     one of them in full
    hence portfolio                  your open positions and trading results
    hence guide                      how to write a Hence thesis yourself
    hence draft new > thesis.json    a blank thesis to fill in
    hence draft check thesis.json    what is wrong with it (nothing is saved)
    hence draft save thesis.json     save it to your Hence library as a private draft
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
    markets.add_argument('--kind', choices=['all', 'perp', 'spot'])
    markets.add_argument('--limit', type=int)

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

    call = sub.add_parser('call', help='call any tool by name', parents=[common])
    call.add_argument('tool')
    call.add_argument('--arg', action='append', default=[], metavar='KEY=VALUE', help='one argument; the value is read as JSON when it is JSON')
    call.add_argument('--args', dest='json_args', metavar='JSON', help='all arguments as one JSON object')
    return p


def _arguments(ns):
    """(tool name, arguments) for the command typed. Options left out are not sent, so the
    server's own defaults apply."""
    def some(**kw):
        return {k: v for k, v in kw.items() if v not in (None, '', False)}
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
        return 'get_my_portfolio', some(history_days=ns.days)
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


def run(argv, out=None, err=None, store=None, sender=None, open_browser=None, stdin=None, confirm=None):
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
