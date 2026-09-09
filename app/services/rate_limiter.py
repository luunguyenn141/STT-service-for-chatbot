from __future__ import annotations

import threading
import time
from collections import defaultdict


class InMemoryRateLimiter:
    """Thread-safe in-memory sliding-window rate limiter."""

    def __init__(self, requests_per_minute: int = 60) -> None:
        self.requests_per_minute = requests_per_minute
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, client_id: str, limit: int | None = None) -> tuple[bool, int, int]:
        """Check if request from client_id is allowed under the rate limit.

        Args:
            client_id: Identifier for the client (e.g. IP address or client ID).
            limit: Optional override for requests_per_minute.

        Returns:
            Tuple of (allowed: bool, remaining_requests: int, retry_after_seconds: int)
        """
        max_requests = self.requests_per_minute if limit is None else limit
        if max_requests <= 0:
            return True, 999999, 0

        now = time.monotonic()
        window_start = now - 60.0

        with self._lock:
            timestamps = self._requests[client_id]
            valid_timestamps = [t for t in timestamps if t > window_start]

            if len(valid_timestamps) >= max_requests:
                earliest = valid_timestamps[0]
                retry_after = max(1, int(60.0 - (now - earliest)))
                self._requests[client_id] = valid_timestamps
                return False, 0, retry_after

            valid_timestamps.append(now)
            self._requests[client_id] = valid_timestamps
            remaining = max(0, max_requests - len(valid_timestamps))
            return True, remaining, 0

    def reset(self) -> None:
        """Clear all tracked request history."""
        with self._lock:
            self._requests.clear()


rate_limiter = InMemoryRateLimiter()
