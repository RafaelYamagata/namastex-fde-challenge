from __future__ import annotations

import datetime as dt
import threading

import httpx
import pytest

from autoseguro.br_formats import cpf_is_valid, format_brl, normalize_cep
from autoseguro.quote import (
    DeferredQuotes,
    IneligibilityCode,
    PlanCatalog,
    QuoteRequest,
    QuoteResult,
    QuoteStatus,
)

TODAY = dt.date(2026, 10, 22)


@pytest.fixture
def catalog() -> PlanCatalog:
    def unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    return PlanCatalog.load("http://quote.test", transport=httpx.MockTransport(unavailable))


def test_catalog_falls_back_to_snapshot(catalog):
    assert catalog.source == "snapshot"
    assert {p["id"] for p in catalog.plans} == {"essencial", "completo", "premium"}


@pytest.mark.parametrize(
    ("idade", "code"),
    [
        (17, IneligibilityCode.IDADE_FORA_DAS_FAIXAS),
        (18, None),
        (75, None),
        (76, IneligibilityCode.IDADE_RECUSADA),
    ],
)
def test_age_eligibility(catalog, idade, code):
    result = catalog.check_age(idade)
    assert (result.code if result else None) == code


@pytest.mark.parametrize(
    ("ano", "code"),
    [
        (2005, IneligibilityCode.VEICULO_RECUSADO),  # 21 years old in 2026
        (2006, None),
        (2026, None),
        (2027, IneligibilityCode.VEICULO_FORA_DAS_FAIXAS),  # next year's model
    ],
)
def test_vehicle_eligibility(catalog, ano, code):
    result = catalog.check_vehicle(ano, today=TODAY)
    assert (result.code if result else None) == code


def test_br_formats():
    assert normalize_cep("01310100") == "01310-100"
    assert normalize_cep("123") is None
    assert cpf_is_valid("389.083.863-43")
    assert not cpf_is_valid("389.083.863-44")
    assert not cpf_is_valid("111.111.111-11")
    assert format_brl(1234.5) == "R$ 1.234,50"
    assert format_brl(111.95) == "R$ 111,95"


class FlakyClient:
    """Stands in for QuoteClient: unavailable for the first `failures` rounds."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.rounds: list[int] = []

    def quote(self, request, *, quote_id, conversation_id, round_no):
        self.rounds.append(round_no)
        status = QuoteStatus.UNAVAILABLE if len(self.rounds) <= self.failures else QuoteStatus.OK
        return QuoteResult(quote_id=quote_id, request=request, status=status)


def _request() -> QuoteRequest:
    return QuoteRequest(
        plano_id="essencial",
        idade=40,
        veiculo_ano=2020,
        cep="01310-100",
        data_inicio=dt.date.today(),
    )


def _run_deferred(client, schedule) -> QuoteResult:
    done: list[QuoteResult] = []
    finished = threading.Event()

    def on_done(result: QuoteResult) -> None:
        done.append(result)
        finished.set()

    deferred = DeferredQuotes(client, schedule_s=schedule)
    deferred.submit(_request(), quote_id="q_test", conversation_id="c_test", on_done=on_done)
    assert finished.wait(5)
    return done[0]


def test_deferred_delivers_when_api_recovers():
    client = FlakyClient(failures=2)
    result = _run_deferred(client, schedule=(0.01, 0.01, 0.01, 0.01))
    assert result.status is QuoteStatus.OK
    assert client.rounds == [1, 2, 3]


def test_deferred_reports_unavailable_when_schedule_runs_out():
    client = FlakyClient(failures=99)
    result = _run_deferred(client, schedule=(0.01, 0.01))
    assert result.status is QuoteStatus.UNAVAILABLE
    assert client.rounds == [1, 2]
