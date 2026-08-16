"""In-process sliding-window rate limiting and brute-force protection.

The limiter is intentionally process-local: it requires no external service,
so it is safe to enable by default and still provides a real control against
request floods and credential-stuffing attempts per API worker. When Redis or
a gateway rate limiter is deployed, its limits apply outside this middleware
and the process-local budget acts as a second line of defense.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitConfig:
    """Tunables for the request and auth-failure budgets."""

    enabled: bool = True
    requests_per_minute: int = 600
    auth_failures_per_minute: int = 10
    auth_failure_window_seconds: int = 60


class _SlidingWindow:
    """Tracks call timestamps per key and prunes entries outside the window."""

    def __init__(self, max_calls: int, window_seconds: int) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> None:
        hits = self._hits[key]
        cutoff = now - self.window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        self._prune(key, now)
        hits = self._hits[key]
        if len(hits) >= self.max_calls:
            return False
        hits.append(now)
        return True

    def count(self, key: str, now: float | None = None) -> int:
        now = now if now is not None else time.monotonic()
        self._prune(key, now)
        return len(self._hits[key])

    def add(self, key: str, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        self._prune(key, now)
        self._hits[key].append(now)

    def clear(self, key: str) -> None:
        self._hits.pop(key, None)


class RateLimiter:
    """Combines a general request budget with an auth-failure lockout budget."""

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self.config = config or RateLimitConfig()
        self._requests = _SlidingWindow(
            self.config.requests_per_minute, 60
        )
        self._auth_failures = _SlidingWindow(
            self.config.auth_failures_per_minute,
            self.config.auth_failure_window_seconds,
        )

    def allow_request(self, key: str) -> bool:
        """True if the key may issue another request under the general budget."""
        if not self.config.enabled:
            return True
        return self._requests.allow(key)

    def is_locked_out(self, key: str) -> bool:
        """True when the key has exhausted the auth-failure budget and is blocked."""
        if not self.config.enabled:
            return False
        return self._auth_failures.count(key) >= self.config.auth_failures_per_minute

    def record_auth_failure(self, key: str) -> None:
        self._auth_failures.add(key)

    def record_auth_success(self, key: str) -> None:
        """Reset the failure budget on a successful authentication."""
        self._auth_failures.clear(key)