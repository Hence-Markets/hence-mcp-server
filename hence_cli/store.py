"""Where this terminal's sign-in is kept: one file, readable only by its owner.

~/.config/hence/credentials.json (or $XDG_CONFIG_HOME/hence, or $HENCE_CONFIG_DIR), mode 0600
in a 0700 directory, the same arrangement the GitHub and AWS command lines use. It is keyed by
server address: a credential is looked up only for the server it was issued for, so pointing
the command at another server never sends it there.
"""
import json
import os
import time

FILE = 'credentials.json'


def directory():
    explicit = os.environ.get('HENCE_CONFIG_DIR')
    if explicit:
        return explicit
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'hence')


class Store:
    def __init__(self, path=None, clock=time.time):
        self.path = path or os.path.join(directory(), FILE)
        self._clock = clock

    def _read(self):
        try:
            if os.stat(self.path).st_mode & 0o077:
                os.chmod(self.path, 0o600)            # someone loosened it: tighten before use
            with open(self.path) as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except Exception:
            # unreadable is "not signed in", and the file is left alone for the person to look at
            return {}
        servers = data.get('servers') if isinstance(data, dict) else None
        return servers if isinstance(servers, dict) else {}

    def _write(self, servers):
        folder = os.path.dirname(self.path)
        os.makedirs(folder, mode=0o700, exist_ok=True)
        tmp = '%s.%d.tmp' % (self.path, os.getpid())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump({'servers': servers}, f)
            os.replace(tmp, self.path)                 # whole file or nothing: never half a credential
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def get(self, server):
        entry = self._read().get(server)
        return dict(entry) if isinstance(entry, dict) else {}

    def update(self, server, **fields):
        servers = self._read()
        entry = dict(servers.get(server) or {})
        entry.update(fields)
        servers[server] = entry
        self._write(servers)

    def forget_tokens(self, server):
        """Drop the sign-in, keep the registration (signing in again reuses it)."""
        servers = self._read()
        entry = dict(servers.get(server) or {})
        if not entry:
            return
        for k in ('access_token', 'refresh_token', 'expires_at', 'scope'):
            entry.pop(k, None)
        servers[server] = entry
        self._write(servers)

    def access_token(self, server):
        """The access token if it is still good for a minute, else None."""
        entry = self.get(server)
        token, expires = entry.get('access_token'), entry.get('expires_at')
        if not isinstance(token, str) or not token:
            return None
        if isinstance(expires, (int, float)) and expires - 60 <= self._clock():
            return None
        return token
