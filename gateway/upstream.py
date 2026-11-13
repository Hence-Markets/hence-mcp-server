"""Reads of the Hence app, with a short cache.

The one rule: a read that did not answer is an error the caller must surface, never an empty
list or a zero. So nothing here returns a default. `Unavailable` means "Hence did not answer";
`NotFound` means "Hence answered that there is no such thing". Only a 200 with a JSON body is
cached, so a failure is retried on the next call instead of being replayed as if it were data.
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config


class Unavailable(Exception):
    """The app did not give a usable answer (down, slow, 5xx, not JSON)."""

    def __init__(self, what, status=None):
        super().__init__(what)
        self.status = status


class NotFound(Exception):
    """The app answered, and the answer is that this does not exist or is not public."""


class Expired(Exception):
    """The app answered 409: a saved selection (a results page) is no longer valid."""


def _fetch(url, timeout):
    """-> (status, bytes). Raises OSError family on transport failure."""
    req = urllib.request.Request(url, headers={
        'Accept': 'application/json',
        # a plain, honest agent string; some edges refuse the default Python one
        'User-Agent': 'Mozilla/5.0 (compatible; HenceMCP/%s)' % config.VERSION,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if hasattr(e, 'read') else b''


class Upstream:
    """`fetch` and `clock` are injectable so the tests need no network and no sleeping."""

    MAX_ENTRIES = 512

    def __init__(self, base=None, fetch=_fetch, clock=time.time):
        self._base = base
        self._fetch = fetch
        self._clock = clock
        self._cache = {}
        self._lock = threading.Lock()

    def base(self):
        return (self._base or config.app_base()).rstrip('/')

    def get_json(self, path, query=None, ttl=0):
        """GET base+path?query as JSON. `path` is built by the caller from validated parts only."""
        qs = urllib.parse.urlencode([(k, v) for k, v in (query or {}).items() if v not in (None, '')])
        url = self.base() + path + ('?' + qs if qs else '')
        now = self._clock()
        if ttl:
            with self._lock:
                hit = self._cache.get(url)
                if hit and hit[0] > now:
                    return hit[1]
        try:
            status, raw = self._fetch(url, config.UPSTREAM_TIMEOUT_S)
        except Exception as e:  # transport: DNS, refused, timeout, TLS
            raise Unavailable('Hence could not be reached (%s)' % e.__class__.__name__)
        if status == 404:
            raise NotFound(path)
        if status == 409:
            raise Expired(path)
        if status != 200:
            raise Unavailable('Hence answered %s' % status, status)
        try:
            data = json.loads(raw.decode('utf-8'))
        except Exception:
            raise Unavailable('Hence gave an answer that could not be read')
        if ttl:
            with self._lock:
                if len(self._cache) >= self.MAX_ENTRIES:
                    # drop what has expired, then the oldest: bounded memory under any traffic
                    for k in [k for k, v in self._cache.items() if v[0] <= now]:
                        self._cache.pop(k, None)
                    while len(self._cache) >= self.MAX_ENTRIES:
                        self._cache.pop(next(iter(self._cache)))
                self._cache[url] = (now + ttl, data)
        return data
