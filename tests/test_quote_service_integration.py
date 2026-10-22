"""The client against the real quote-service (spawned with uv, no Docker needed)."""

from __future__ import annotations

import datetime as dt

import pytest

from autoseguro.local_service import run_quote_service
from autoseguro.quote import CircuitBreaker, QuoteClient, QuoteRequest, QuoteStatus

pytestmark = pytest.mark.integration


def _next_month_15th() -> dt.date:
    today = dt.date.today()
    first_next = (today.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    return first_next.replace(day=15)


@pytest.fixture(scope="module")
def stable_api():
    with run_quote_service(failure_rate=0, slow_rate=0) as url:
        yield url


def test_happy_path_matches_the_rules(stable_api):
    client = QuoteClient(stable_api)
    req = QuoteRequest(
        plano_id="completo",
        idade=35,
        veiculo_ano=dt.date.today().year - 1,
        cep="07123-456",
        data_inicio=_next_month_15th(),
    )
    result = client.quote(req)
    assert result.status is QuoteStatus.OK
    # 209.90 base x 1.0 (age 30-59) x 1.0 (car <= 5 years) x 1.30 (CEP prefix 07)
    assert result.data["premio_mensal"] == round(209.90 * 1.30, 2)
    assert result.data["multiplicadores"]["regiao"] == 1.30
    assert "primeiro_pagamento_pro_rata" in result.data  # starts mid-month


def test_refusal_comes_back_as_refused(stable_api):
    client = QuoteClient(stable_api)
    req = QuoteRequest(
        plano_id="essencial",
        idade=80,
        veiculo_ano=2020,
        cep="01310-100",
        data_inicio=dt.date.today(),
    )
    result = client.quote(req)
    assert result.status is QuoteStatus.REFUSED
    assert "75 anos" in result.detail


def test_total_outage_ends_unavailable_and_opens_the_circuit():
    with run_quote_service(failure_rate=1.0, slow_rate=0) as url:
        breaker = CircuitBreaker(min_calls=3)
        client = QuoteClient(url, breaker=breaker)
        req = QuoteRequest(
            plano_id="essencial",
            idade=40,
            veiculo_ano=2020,
            cep="01310-100",
            data_inicio=dt.date.today(),
        )
        result = client.quote(req)
        assert result.status is QuoteStatus.UNAVAILABLE and result.data is None
        assert breaker.state == "open"
        assert client.quote(req).attempts == []  # fails fast while open
