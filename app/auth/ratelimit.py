import time
from collections import defaultdict, deque


class RateLimiter:
    """Fenêtre glissante en mémoire.

    Provisoire : à remplacer par une implémentation Redis (partagée entre
    workers Gunicorn) dès que Redis est installé. L'interface reste la même.
    """

    def __init__(self, max_hits: int, window_seconds: int):
        self.max_hits = max_hits
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str) -> deque[float]:
        q = self._hits[key]
        cutoff = time.monotonic() - self.window
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def is_limited(self, key: str) -> bool:
        return len(self._prune(key)) >= self.max_hits

    def hit(self, key: str) -> None:
        self._prune(key).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)

    def clear(self) -> None:
        self._hits.clear()


# 5 échecs / 15 min, par IP et par email (cf. ARCHITECTURE.md).
login_limiter = RateLimiter(max_hits=5, window_seconds=15 * 60)
register_limiter = RateLimiter(max_hits=10, window_seconds=60 * 60)
