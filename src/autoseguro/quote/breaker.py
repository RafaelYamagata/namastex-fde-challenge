"""Circuit breaker shared by every conversation that talks to the quote API."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from autoseguro.obs import log_event


class CircuitBreaker:
    """Stops calling a dependency that is clearly down.

    The circuit opens when at least `failure_ratio` of the last `window` calls failed (once
    `min_calls` have been seen). While open, calls fail fast without touching the API; after
    `reset_timeout_s` a single probe goes through (half-open): success closes the circuit,
    failure reopens it.

    Why a failure *rate* and not "N failures in a row": on a normal day this API fails ~30%
    of attempts (20% errors + 10% that hang past our timeout), so a run of 5 consecutive
    failures shows up regularly and would block every quote for no reason. With a 90%
    threshold over 30 calls, ordinary flakiness never trips it, and a real outage trips it
    after 10 calls.
    """

    def __init__(
        self,
        window: int = 30,
        min_calls: int = 10,
        failure_ratio: float = 0.9,
        reset_timeout_s: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
        name: str = "quote_api",
    ) -> None:
        self.min_calls = min_calls
        self.failure_ratio = failure_ratio
        self.reset_timeout_s = reset_timeout_s
        self.name = name
        self._clock = clock
        self._lock = threading.Lock()
        self._outcomes: deque[bool] = deque(maxlen=window)  # True = failed call
        self._state = "closed"
        self._opened_at = 0.0
        self._probe_in_flight = False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def allow_request(self) -> bool:
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                if self._clock() - self._opened_at < self.reset_timeout_s:
                    return False
                self._state = "half_open"
                log_event("circuit_half_open", breaker=self.name)
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True

    def record_success(self) -> None:
        with self._lock:
            if self._state == "open":
                return  # a call that started before the circuit opened; only the probe decides
            self._probe_in_flight = False
            if self._state == "closed":
                self._outcomes.append(False)
                return
            log_event("circuit_closed", breaker=self.name)
            self._state = "closed"
            self._outcomes.clear()

    def record_failure(self) -> None:
        with self._lock:
            self._probe_in_flight = False
            if self._state == "open":
                return  # a call that started before the circuit opened
            if self._state == "half_open":
                self._open("probe_failed")
                return
            self._outcomes.append(True)
            calls, failures = len(self._outcomes), sum(self._outcomes)
            if calls >= self.min_calls and failures / calls >= self.failure_ratio:
                self._open(f"{failures}/{calls} calls failed")

    def _open(self, reason: str) -> None:
        log_event("circuit_opened", breaker=self.name, reason=reason)
        self._state = "open"
        self._opened_at = self._clock()
