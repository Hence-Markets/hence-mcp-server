"""A sliding-window flood guard, per client address.

Not a per-person quota: hosted assistants call from a handful of shared addresses, so many
people sit behind one key. Per-person limits arrive with identity and are the app's to enforce.
"""
import threading
import time
from collections import deque


class RateLimiter:
    MAX_KEYS = 20000

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key, limit, window=60.0):
        """True and counted, or False when `key` already made `limit` calls in the window."""
        now = self._clock()
        with self._lock:
            q = self._hits.get(key)
            if q is None:
                if len(self._hits) >= self.MAX_KEYS:
                    # under a spray of addresses, forget the idle ones rather than grow forever
                    for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - window]:
                        del self._hits[k]
                    if len(self._hits) >= self.MAX_KEYS:
                        return False
                q = self._hits[key] = deque()
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True
