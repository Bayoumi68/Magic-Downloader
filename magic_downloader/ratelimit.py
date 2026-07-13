"""A shared, thread-safe download speed limiter (token bucket).

One instance is shared by every active download thread so the configured cap is
an *aggregate* limit across the whole app — matching IDM's global speed limiter.
A rate of 0 means unlimited.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, rate_bps: float = 0.0) -> None:
        self._rate = max(0.0, float(rate_bps))
        self._allowance = self._rate
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def set_rate(self, rate_bps: float) -> None:
        with self._lock:
            self._rate = max(0.0, float(rate_bps))
            self._allowance = self._rate
            self._last = time.monotonic()

    @property
    def rate(self) -> float:
        return self._rate

    def throttle(self, n_bytes: int) -> None:
        """Block just long enough to keep the aggregate rate under the cap."""
        if self._rate <= 0 or n_bytes <= 0:
            return
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._last = now
            self._allowance += elapsed * self._rate
            if self._allowance > self._rate:
                self._allowance = self._rate
            self._allowance -= n_bytes
            sleep_for = (-self._allowance / self._rate) if self._allowance < 0 else 0.0
        if sleep_for > 0:
            # Cap a single sleep so pause/cancel stay responsive.
            time.sleep(min(sleep_for, 0.5))
