"""Retry policy, error classification, circuit breaker and cache, against a scripted API.

No network and no real sleeping: the transport plays back a script and a fake clock advances
by the simulated latency of each call.
"""

from __future__ import annotations

import datetime as dt
import random

import httpx
import pytest

from autoseguro.clock import today as business_today
from autoseguro.quote import CircuitBreaker, QuoteClient, QuoteRequest, QuoteStatus, RetryPolicy

OK_BODY = {"plano_id": "completo", "premio_mensal": 209.9, "franquia": 3000, "coberturas": []}
REFUSED_BODY = {
    "error": "cotacao_recusada",
    "motivo": "Idade acima do limite de aceitacao (75 anos).",
}
VALIDATION_BODY = {"detail": [{"loc": ["body", "veiculo_ano"], "msg": "Input should be >= 1950"}]}
UNAVAILABLE_BODY = {"error": "upstream_unavailable", "message": "Tente novamente."}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class ScriptedAPI:
    """Each step is (status, body), "timeout", or "connect_error". Latency is simulated."""

    def __init__(self, clock: FakeClock, steps: list, latency_s: float = 0.01) -> None:
        self.clock, self.steps, self.latency_s = clock, list(steps), latency_s
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        step = self.steps.pop(0)
        if step == "timeout":
            self.clock.now += 2.5
            raise httpx.ReadTimeout("timed out", request=request)
        if step == "connect_error":
            raise httpx.ConnectError("refused", request=request)
        self.clock.now += self.latency_s
        status, body = step
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)


def make_client(steps: list, *, policy: RetryPolicy | None = None, breaker=None):
    clock = FakeClock()
    api = ScriptedAPI(clock, steps)
    client = QuoteClient(
        "http://quote.test",
        policy=policy,
        breaker=breaker or CircuitBreaker(clock=clock),
        transport=httpx.MockTransport(api),
        sleep=clock.sleep,
        clock=clock,
        rng=random.Random(0),
    )
    return client, api, clock


def request(**overrides) -> QuoteRequest:
    data = {
        "plano_id": "completo",
        "idade": 35,
        "veiculo_ano": 2022,
        "cep": "01310-100",
        "data_inicio": business_today() + dt.timedelta(days=10),
    }
    return QuoteRequest(**{**data, **overrides})


def test_first_try_success():
    client, api, _ = make_client([(200, OK_BODY)])
    result = client.quote(request())
    assert result.status is QuoteStatus.OK
    assert result.data["premio_mensal"] == 209.9
    assert api.calls == 1 and len(result.attempts) == 1


def test_retries_5xx_then_succeeds():
    client, api, clock = make_client(
        [(503, UNAVAILABLE_BODY), (502, UNAVAILABLE_BODY), (200, OK_BODY)]
    )
    result = client.quote(request())
    assert result.status is QuoteStatus.OK
    assert [a.error for a in result.attempts] == ["http_503", "http_502", None]
    assert len(clock.sleeps) == 2  # backoff between attempts


def test_timeout_is_retried():
    client, _, _ = make_client(["timeout", (200, OK_BODY)])
    result = client.quote(request())
    assert result.status is QuoteStatus.OK
    assert result.attempts[0].error == "timeout" and result.attempts[0].status_code is None


def test_connection_error_is_retried():
    client, _, _ = make_client(["connect_error", (200, OK_BODY)])
    assert client.quote(request()).status is QuoteStatus.OK


def test_garbled_200_is_retried():
    client, _, _ = make_client([(200, "<html>proxy error</html>"), (200, OK_BODY)])
    result = client.quote(request())
    assert result.status is QuoteStatus.OK
    assert result.attempts[0].error == "bad_body"


def test_gives_up_after_max_attempts_without_a_price():
    client, api, _ = make_client([(500, UNAVAILABLE_BODY)] * 4)
    result = client.quote(request())
    assert result.status is QuoteStatus.UNAVAILABLE
    assert result.data is None
    assert api.calls == 4
    assert result.detail == "http_500"


def test_business_refusal_is_not_retried():
    client, api, _ = make_client([(422, REFUSED_BODY)])
    result = client.quote(request(idade=80))
    assert result.status is QuoteStatus.REFUSED
    assert result.detail == REFUSED_BODY["motivo"]
    assert api.calls == 1


def test_validation_422_is_invalid_not_refused():
    client, api, _ = make_client([(422, VALIDATION_BODY)])
    result = client.quote(request())
    assert result.status is QuoteStatus.INVALID
    assert api.calls == 1


def test_400_is_invalid():
    client, api, _ = make_client([(400, {"error": "payload_invalido", "detalhe": "x"})])
    assert client.quote(request()).status is QuoteStatus.INVALID
    assert api.calls == 1


def test_total_deadline_stops_retries():
    policy = RetryPolicy(max_attempts=10, deadline_s=6.0)
    client, api, _ = make_client(["timeout"] * 10, policy=policy)
    result = client.quote(request())
    assert result.status is QuoteStatus.UNAVAILABLE
    assert api.calls < 10  # each timeout burns 2.5s of a 6s budget
    assert result.detail in {"timeout", "deadline_exceeded"}


def test_backoff_stays_within_cap():
    policy = RetryPolicy(max_attempts=6, deadline_s=1000)
    client, _, clock = make_client(
        [(500, UNAVAILABLE_BODY)] * 6, policy=policy, breaker=CircuitBreaker(min_calls=100)
    )
    client.quote(request())
    assert all(0 <= s <= policy.backoff_cap_s for s in clock.sleeps)


def test_identical_quote_is_served_from_cache():
    client, api, _ = make_client([(200, OK_BODY)])
    first = client.quote(request())
    second = client.quote(request())
    assert first.status is second.status is QuoteStatus.OK
    assert second.cached and api.calls == 1


def test_breaker_opens_and_fails_fast():
    clock = FakeClock()
    breaker = CircuitBreaker(min_calls=3, reset_timeout_s=30, clock=clock)
    api = ScriptedAPI(clock, [(503, UNAVAILABLE_BODY)] * 3 + [(200, OK_BODY)])
    client = QuoteClient(
        "http://quote.test",
        breaker=breaker,
        transport=httpx.MockTransport(api),
        sleep=clock.sleep,
        clock=clock,
        rng=random.Random(0),
    )

    first = client.quote(request())
    assert first.status is QuoteStatus.UNAVAILABLE and first.circuit_open
    assert api.calls == 3 and breaker.state == "open"

    second = client.quote(request())
    assert second.circuit_open and second.attempts == [] and api.calls == 3  # no call at all

    clock.now += 31  # after the reset timeout one probe goes through
    third = client.quote(request())
    assert third.status is QuoteStatus.OK and breaker.state == "closed"


def test_breaker_ignores_ordinary_flakiness():
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    for i in range(300):  # ~30% failures, the API's normal day
        if i % 10 < 3:
            breaker.record_failure()
        else:
            breaker.record_success()
        assert breaker.state == "closed"


def test_failed_probe_reopens_the_circuit():
    clock = FakeClock()
    breaker = CircuitBreaker(min_calls=2, reset_timeout_s=10, clock=clock)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "open"
    clock.now += 11
    assert breaker.allow_request()  # the probe
    assert not breaker.allow_request()  # only one probe at a time
    breaker.record_failure()
    assert breaker.state == "open" and not breaker.allow_request()


def test_refusal_counts_as_healthy_for_the_breaker():
    clock = FakeClock()
    breaker = CircuitBreaker(min_calls=2, clock=clock)
    client, _, _ = make_client([(500, UNAVAILABLE_BODY), (422, REFUSED_BODY)], breaker=breaker)
    client.quote(request())
    assert breaker.state == "closed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cep", "7123-456"),  # lost its leading zero: would be priced for the wrong region
        ("cep", "CEP 01310-100"),
        ("plano_id", "ouro"),
        ("data_inicio", business_today() - dt.timedelta(days=1)),
    ],
)
def test_request_rejects_inputs_the_api_would_misprice(field, value):
    with pytest.raises(ValueError):
        request(**{field: value})


def test_request_normalizes_cep():
    assert request(cep="01310100").cep == "01310-100"
    assert request(cep="01.310-100").cep == "01310-100"
