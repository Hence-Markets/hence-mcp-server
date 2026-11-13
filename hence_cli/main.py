"""hence: the command.

    hence theses search <words>      public theses about something
    hence theses featured            today's featured theses
    hence thesis <id or link>        one thesis in full
    hence markets <words>            what can be traded on Hence, and where
    hence login | logout | whoami    connect this terminal to your Hence account
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


def run(argv, out=None, err=None, store=None, sender=None, open_browser=None):
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
