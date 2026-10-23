"""Background retries for quotes the inline attempts could not get.

The lead has already been told the truth (the quote system is unstable, the price will come
here as soon as it's out). Nothing approximate is ever sent in the meantime. Each round is a
full `QuoteClient.quote` call, so the circuit breaker still applies: while it is open a round
costs nothing. When the schedule runs out, `on_done` receives the last UNAVAILABLE result and
the caller hands the conversation to a human.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence

from autoseguro.obs import log_event
from autoseguro.quote.client import QuoteClient
from autoseguro.quote.models import QuoteRequest, QuoteResult, QuoteStatus

# Waits between rounds: ~4.5 minutes in total, inside the 5-minute handoff SLA.
DEFAULT_SCHEDULE_S: tuple[float, ...] = (10, 20, 40, 80, 120)


class DeferredQuotes:
    def __init__(self, client: QuoteClient, schedule_s: Sequence[float] = DEFAULT_SCHEDULE_S):
        self._client = client
        self._schedule = tuple(schedule_s)
        self._stop = threading.Event()
        self._threads: dict[str, threading.Thread] = {}

    def submit(
        self,
        request: QuoteRequest,
        *,
        quote_id: str,
        conversation_id: str | None,
        on_done: Callable[[QuoteResult], None],
        on_round: Callable[[QuoteResult, int], None] | None = None,
    ) -> None:
        """Retry in the background. `on_round` sees every round (for the trace); `on_done`
        gets the final result once: a price, a definitive answer, or UNAVAILABLE."""
        log_event(
            "quote_deferred",
            quote_id=quote_id,
            conversation_id=conversation_id,
            schedule_s=list(self._schedule),
        )
        thread = threading.Thread(
            target=self._run,
            args=(request, quote_id, conversation_id, on_done, on_round),
            name=f"deferred-{quote_id}",
            daemon=True,
        )
        self._threads[quote_id] = thread
        thread.start()

    def max_duration_s(self) -> float:
        """Upper bound for one background quote: waits plus a full inline budget per round.
        A record still pending after this is stuck (e.g. the process restarted)."""
        deadline = getattr(getattr(self._client, "policy", None), "deadline_s", 12.0)
        return sum(self._schedule) + len(self._schedule) * deadline + 30

    def pending(self) -> list[str]:
        return list(self._threads)

    def wait(self, timeout: float | None = None) -> None:
        for thread in list(self._threads.values()):
            thread.join(timeout)

    def shutdown(self) -> None:
        self._stop.set()

    def _run(
        self,
        request: QuoteRequest,
        quote_id: str,
        conversation_id: str | None,
        on_done: Callable[[QuoteResult], None],
        on_round: Callable[[QuoteResult, int], None] | None,
    ) -> None:
        result: QuoteResult | None = None
        try:
            for round_no, delay in enumerate(self._schedule, start=1):
                if self._stop.wait(delay):
                    log_event("quote_deferred_cancelled", quote_id=quote_id, round=round_no)
                    return
                try:
                    result = self._client.quote(
                        request,
                        quote_id=quote_id,
                        conversation_id=conversation_id,
                        round_no=round_no,
                    )
                except Exception as exc:  # a bug must not end the retries silently
                    log_event("quote_deferred_error", quote_id=quote_id, error=repr(exc))
                    result = QuoteResult(
                        quote_id=quote_id,
                        request=request,
                        status=QuoteStatus.UNAVAILABLE,
                        detail=repr(exc),
                    )
                if on_round is not None:
                    _safely(on_round, result, round_no, quote_id=quote_id)
                if result.status is not QuoteStatus.UNAVAILABLE:
                    break
            if result is None:  # empty schedule
                result = QuoteResult(
                    quote_id=quote_id, request=request, status=QuoteStatus.UNAVAILABLE
                )
            log_event(
                "quote_deferred_done",
                quote_id=quote_id,
                conversation_id=conversation_id,
                status=result.status,
            )
            _safely(on_done, result, quote_id=quote_id)
        finally:
            self._threads.pop(quote_id, None)


def _safely(callback: Callable[..., None], *args: object, quote_id: str) -> None:
    try:
        callback(*args)
    except Exception as exc:
        log_event("quote_deferred_callback_error", quote_id=quote_id, error=repr(exc))
