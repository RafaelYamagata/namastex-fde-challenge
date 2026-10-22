"""Resilience benchmark against the real quote-service.

For each failure rate it starts a fresh service and runs the same set of quotes through two
clients: `QuoteClient` (timeouts, retries, circuit breaker) and a naive client (one call,
generous timeout, which is what most first versions do). The interesting columns are how many
quotes came back with a price, and how long the lead waited.
"""

from __future__ import annotations

import datetime as dt
import statistics
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx

from autoseguro.local_service import run_quote_service
from autoseguro.quote.client import QuoteClient
from autoseguro.quote.models import PLAN_IDS, QuoteRequest, QuoteStatus


@dataclass
class BenchRow:
    client: str
    failure_rate: float
    n: int
    with_price: int
    without_price: int
    mean_attempts: float
    p50_s: float
    p95_s: float
    max_s: float

    @property
    def success_pct(self) -> float:
        return 100 * self.with_price / self.n


def _requests(n: int) -> list[QuoteRequest]:
    start = dt.date.today() + dt.timedelta(days=7)
    ceps = ["01310-100", "07123-456", "21040-020", "30130-000", "59010-000"]
    # Distinct payloads, so the client's cache never answers for the API.
    return [
        QuoteRequest(
            plano_id=PLAN_IDS[i % 3],
            idade=25 + i % 50,
            veiculo_ano=2008 + (i // 50) % 18,
            cep=ceps[(i // 7) % len(ceps)],
            data_inicio=start,
        )
        for i in range(n)
    ]


def _summarize(name: str, rate: float, outcomes: list[tuple[bool, int, float]]) -> BenchRow:
    latencies = sorted(o[2] for o in outcomes)
    ok = sum(1 for o in outcomes if o[0])
    return BenchRow(
        client=name,
        failure_rate=rate,
        n=len(outcomes),
        with_price=ok,
        without_price=len(outcomes) - ok,
        mean_attempts=statistics.fmean(o[1] for o in outcomes),
        p50_s=latencies[len(latencies) // 2],
        p95_s=latencies[int(len(latencies) * 0.95) - 1],
        max_s=latencies[-1],
    )


def _timed(fn: Callable[[QuoteRequest], tuple[bool, int]]) -> Callable[[QuoteRequest], tuple]:
    def run(req: QuoteRequest) -> tuple[bool, int, float]:
        t0 = time.monotonic()
        ok, attempts = fn(req)
        return ok, attempts, time.monotonic() - t0

    return run


def run_bench(
    failure_rates: Sequence[float] = (0.2, 0.5, 1.0),
    n: int = 200,
    concurrency: int = 8,
    seed: int = 42,
) -> list[BenchRow]:
    rows: list[BenchRow] = []
    requests = _requests(n)
    for rate in failure_rates:
        with run_quote_service(failure_rate=rate, slow_rate=0.1, seed=seed) as url:
            client = QuoteClient(url)

            def resilient(req: QuoteRequest, client: QuoteClient = client) -> tuple[bool, int]:
                result = client.quote(req)
                return result.status is QuoteStatus.OK, len(result.attempts)

            http = httpx.Client(base_url=url, timeout=30)

            def naive(req: QuoteRequest, http: httpx.Client = http) -> tuple[bool, int]:
                return http.post("/quote", json=req.payload()).status_code == 200, 1

            with ThreadPoolExecutor(concurrency) as pool:
                rows.append(
                    _summarize("resiliente", rate, list(pool.map(_timed(resilient), requests)))
                )
                rows.append(_summarize("ingênuo", rate, list(pool.map(_timed(naive), requests))))
            client.close()
            http.close()
    return rows


def to_markdown(rows: list[BenchRow]) -> str:
    lines = [
        "| Taxa de falha | Cliente | Com preço | Sem preço | Tentativas (média) "
        "| Espera p50 | Espera p95 | Espera máx |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.failure_rate:.0%} | {r.client} | {r.with_price}/{r.n} ({r.success_pct:.1f}%) "
            f"| {r.without_price} | {r.mean_attempts:.2f} | {r.p50_s:.2f}s | {r.p95_s:.2f}s "
            f"| {r.max_s:.2f}s |"
        )
    return "\n".join(lines)
