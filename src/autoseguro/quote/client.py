"""Resilient client for POST /quote.

What the API does (see quote-service/app/main.py): ~20% of calls fail with 500/502/503 and
~10% hang for 8 seconds before answering. The quote is a pure function of the payload (and
of today's date), so repeating a call is always safe.

Policy, in order:
1. Cache: an identical payload quoted today is answered without calling the API.
2. Circuit breaker: if the API is clearly down, fail fast instead of piling on.
3. Each attempt has a short timeout (healthy calls answer in milliseconds; waiting out the 8s
   hang is worse than retrying).
4. Retry only what can succeed on retry: 5xx, 429, timeouts, connection errors and garbled
   bodies. A 4xx is an answer (refusal or bad payload) and is returned as such.
5. Exponential backoff with full jitter between attempts, all inside a total deadline.

The client never raises for expected failures and never invents a price: when it gives up,
it returns status UNAVAILABLE with every attempt recorded.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from autoseguro.clock import today as business_today
from autoseguro.obs import log_event, new_id, utcnow
from autoseguro.quote.breaker import CircuitBreaker
from autoseguro.quote.models import QuoteAttempt, QuoteRequest, QuoteResult, QuoteStatus


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    connect_timeout_s: float = 1.0
    read_timeout_s: float = 2.5
    backoff_base_s: float = 0.25
    backoff_cap_s: float = 2.0
    deadline_s: float = 12.0  # total budget for one quote, retries included
    min_attempt_s: float = 0.5  # don't start an attempt with less budget left than this


class QuoteClient:
    def __init__(
        self,
        base_url: str,
        *,
        policy: RetryPolicy | None = None,
        breaker: CircuitBreaker | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.policy = policy or RetryPolicy()
        self.breaker = breaker or CircuitBreaker()
        self._http = httpx.Client(base_url=base_url, transport=transport)
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()
        self._cache: dict[tuple[str, dt.date], dict[str, Any]] = {}
        self._cache_lock = threading.Lock()

    def close(self) -> None:
        self._http.close()

    def quote(
        self,
        request: QuoteRequest,
        *,
        quote_id: str | None = None,
        conversation_id: str | None = None,
        round_no: int = 0,
    ) -> QuoteResult:
        """Quote one plan. `round_no` > 0 marks a background (deferred) round in the logs."""
        quote_id = quote_id or new_id("q")
        ctx = {"quote_id": quote_id, "conversation_id": conversation_id, "round": round_no}
        result = QuoteResult(quote_id=quote_id, request=request, status=QuoteStatus.UNAVAILABLE)

        cached = self._cache_get(request)
        if cached is not None:
            result.status, result.data, result.cached = QuoteStatus.OK, cached, True
            log_event("quote_finished", **ctx, status=result.status, attempts=0, cached=True)
            return result

        policy = self.policy
        started = self._clock()
        for number in range(1, policy.max_attempts + 1):
            remaining = policy.deadline_s - (self._clock() - started)
            if remaining < policy.min_attempt_s:
                result.detail = "deadline_exceeded"
                break
            if not self.breaker.allow_request():
                result.circuit_open, result.detail = True, "circuit_open"
                break

            attempt, body = self._send(number, request, min(policy.read_timeout_s, remaining))
            result.attempts.append(attempt)
            log_event(
                "quote_attempt",
                **ctx,
                attempt=number,
                status_code=attempt.status_code,
                error=attempt.error,
                latency_ms=round(attempt.latency_ms, 1),
            )

            if attempt.error is not None:  # retryable: the API did not give an answer
                self.breaker.record_failure()
                result.detail = attempt.error
                if number < policy.max_attempts:
                    left = policy.deadline_s - (self._clock() - started) - policy.min_attempt_s
                    self._sleep(max(0.0, min(self._backoff(number), left)))
                continue

            self.breaker.record_success()
            if attempt.status_code == 200:
                result.status, result.data, result.detail = QuoteStatus.OK, body, None
                self._cache_put(request, body)
            elif attempt.status_code == 422 and body.get("error") == "cotacao_recusada":
                result.status, result.detail = QuoteStatus.REFUSED, body.get("motivo")
            else:  # 400 payload_invalido, 422 from FastAPI validation, other 4xx
                result.status = QuoteStatus.INVALID
                result.detail = json.dumps(body, ensure_ascii=False)[:500]
            break

        log_event(
            "quote_finished",
            **ctx,
            status=result.status,
            attempts=len(result.attempts),
            detail=result.detail,
            circuit_open=result.circuit_open,
        )
        return result

    def _send(
        self, number: int, request: QuoteRequest, read_timeout: float
    ) -> tuple[QuoteAttempt, dict[str, Any]]:
        started_at = utcnow()
        t0 = self._clock()
        status_code: int | None = None
        body: dict[str, Any] = {}
        error: str | None = None
        timeout = httpx.Timeout(
            read_timeout, connect=min(self.policy.connect_timeout_s, read_timeout)
        )
        try:
            response = self._http.post("/quote", json=request.payload(), timeout=timeout)
            status_code = response.status_code
            try:
                parsed = response.json()
                body = parsed if isinstance(parsed, dict) else {}
                if not isinstance(parsed, dict):
                    error = "bad_body"
            except ValueError:
                error = "bad_body"
            if status_code >= 500 or status_code == 429:
                error = f"http_{status_code}"
            elif status_code < 300 and error is None and "premio_mensal" not in body:
                error = "bad_body"
            elif status_code >= 300 and error == "bad_body":
                error = None  # a 4xx with a non-JSON body is still an answer
        except httpx.TimeoutException:
            error = "timeout"
        except httpx.TransportError as exc:
            error = f"connect_error:{type(exc).__name__}"
        latency_ms = (self._clock() - t0) * 1000
        return QuoteAttempt(number, started_at, latency_ms, status_code, error), body

    def _backoff(self, attempt_number: int) -> float:
        p = self.policy
        return self._rng.uniform(
            0, min(p.backoff_cap_s, p.backoff_base_s * 2 ** (attempt_number - 1))
        )

    # The API computes vehicle age from today's date, so a cached quote is only valid today.
    def _cache_key(self, request: QuoteRequest) -> tuple[str, dt.date]:
        return json.dumps(request.payload(), sort_keys=True), business_today()

    def _cache_get(self, request: QuoteRequest) -> dict[str, Any] | None:
        with self._cache_lock:
            return self._cache.get(self._cache_key(request))

    def _cache_put(self, request: QuoteRequest, body: dict[str, Any]) -> None:
        with self._cache_lock:
            self._cache[self._cache_key(request)] = body
