"""Edge cases found in the code review: each test reproduces the failure scenario."""

from __future__ import annotations

import datetime as dt
import random

import httpx
import pytest
from fakes import HAPPY, START, FakeQuoteClient, ScriptedLLM, catalog_snapshot, decisions, nlu
from fakes import make_agent as _make_agent

from autoseguro.agent.guardrails import check_outbound, unbacked_amounts
from autoseguro.agent.state import HandoffReason, Stage
from autoseguro.obs import utcnow
from autoseguro.quote import CircuitBreaker, DeferredQuotes, PlanCatalog, QuoteResult, QuoteStatus


@pytest.fixture
def catalog() -> PlanCatalog:
    return catalog_snapshot()


@pytest.fixture
def make_agent():
    agents = []

    def make(*args, **kwargs):
        agent, sent = _make_agent(*args, **kwargs)
        agents.append(agent)
        return agent, sent

    yield make
    for agent in agents:  # stop background retries left running by a test
        agent.deferred.shutdown()


def _quote_everything(agent, cid):
    for m in ["oi", "Onix 2021", "35 anos, cep 01310-100, dia 15"]:
        agent.handle(cid, [m])


def test_start_date_in_the_past_is_asked_again_not_a_crash(tmp_path, catalog, make_agent):
    # The lead quoted on one day and came back after the start date had passed.
    llm = ScriptedLLM(HAPPY + [nlu(intencoes=["outro_plano"])])
    agent, _ = make_agent(tmp_path, catalog, llm)
    cid = agent.start()
    _quote_everything(agent, cid)
    state = agent.store.load_state(cid)
    state.data.data_inicio = dt.date.today() - dt.timedelta(days=1)
    agent.store.save_state(state)

    out = agent.handle(cid, ["e o premium?"])
    state = agent.store.load_state(cid)
    assert state.handoff is None and "problema técnico" not in out[-1]
    assert state.data.data_inicio is None and state.current_quotes() == {}
    assert decisions(agent, cid)[-1] == "R18_coletar_dados"


def test_stuck_pending_quote_is_redone_after_its_deadline(tmp_path, catalog, make_agent):
    # E.g. the process restarted: the retry thread is gone, the record still says pending.
    client = FakeQuoteClient(catalog, [QuoteStatus.UNAVAILABLE] * 3)
    llm = ScriptedLLM(HAPPY + [nlu()])
    agent, _ = make_agent(tmp_path, catalog, llm, client, schedule=(60,))
    cid = agent.start()
    _quote_everything(agent, cid)
    state = agent.store.load_state(cid)
    assert len(state.pending_quotes()) == 3
    for q in state.quotes.values():
        q.requested_at = (utcnow() - dt.timedelta(hours=1)).isoformat()
    agent.store.save_state(state)

    out = agent.handle(cid, ["e aí?"])
    assert decisions(agent, cid)[-1] == "R19_cotar"
    assert "R$ 209,90/mês" in out[-1]


def test_pending_quotes_for_old_data_do_not_block_new_ones(tmp_path, catalog, make_agent):
    client = FakeQuoteClient(catalog, [QuoteStatus.UNAVAILABLE] * 3)
    llm = ScriptedLLM(HAPPY + [nlu(idade=36), nlu(intencoes=["reenviar_cotacao"])])
    agent, _ = make_agent(tmp_path, catalog, llm, client, schedule=(60,))
    cid = agent.start()
    _quote_everything(agent, cid)
    agent.handle(cid, ["na verdade tenho 36"])  # new data: quoted again, inline
    agent.handle(cid, ["manda de novo os valores"])
    assert decisions(agent, cid)[-2:] == ["R19_cotar", "R20_reapresentar"]


def test_a_plan_pending_in_the_background_is_not_quoted_twice(tmp_path, catalog, make_agent):
    client = FakeQuoteClient(catalog, unavailable_plans={"completo"})
    llm = ScriptedLLM(HAPPY + [nlu(veiculo_modelo="Onix Plus")])
    agent, _ = make_agent(tmp_path, catalog, llm, client, schedule=(60,))
    cid = agent.start()
    _quote_everything(agent, cid)
    calls = client.calls
    agent.handle(cid, ["ah, é um Onix Plus"])  # changes a field that doesn't affect the price
    assert client.calls == calls
    assert decisions(agent, cid)[-1] == "R16_cotacao_pendente"


def test_landline_number_does_not_replace_the_cep(tmp_path, catalog, make_agent):
    llm = ScriptedLLM([nlu(veiculo_ano=2021), nlu()])
    agent, _ = make_agent(tmp_path, catalog, llm)
    cid = agent.start()
    agent.handle(cid, ["Onix 2021, cep 01310-100"])
    agent.handle(cid, ["meu fixo é 11 34567890"])
    assert agent.store.load_state(cid).data.cep == "01310-100"


def test_bare_cep_as_the_whole_answer_still_counts(tmp_path, catalog, make_agent):
    llm = ScriptedLLM([nlu(veiculo_ano=2021, idade=40)])
    agent, _ = make_agent(tmp_path, catalog, llm)
    cid = agent.start()
    agent.handle(cid, ["01310100"])
    assert agent.store.load_state(cid).data.cep == "01310-100"


def test_reopened_conversation_is_not_left_as_lost(tmp_path, catalog, make_agent):
    llm = ScriptedLLM([nlu(intencoes=["desistir"]), nlu(veiculo_ano=2021)])
    agent, _ = make_agent(tmp_path, catalog, llm)
    cid = agent.start()
    agent.handle(cid, ["não tenho mais interesse"])
    assert agent.store.load_state(cid).outcome == "perdido"
    agent.handle(cid, ["pensei melhor, é um Onix 2021"])
    state = agent.store.load_state(cid)
    assert state.outcome is None and state.stage is Stage.COLETANDO


def test_background_quote_is_not_sent_after_the_lead_gave_up(tmp_path, catalog, make_agent):
    client = FakeQuoteClient(catalog, [QuoteStatus.UNAVAILABLE] * 3)
    llm = ScriptedLLM(HAPPY + [nlu(intencoes=["desistir"])])
    agent, sent = make_agent(tmp_path, catalog, llm, client, schedule=(0.2,))
    cid = agent.start()
    _quote_everything(agent, cid)
    agent.handle(cid, ["deixa pra lá"])
    agent.deferred.wait(5)
    assert not any(s.startswith("Saiu!") for s in sent)
    assert agent.store.load_state(cid).stage is Stage.ENCERRADO


def test_failed_delivery_does_not_lose_the_turn(tmp_path, catalog, make_agent):
    llm = ScriptedLLM([nlu(intencoes=["pedir_humano"])])
    agent, _ = make_agent(tmp_path, catalog, llm)
    agent._send_cb = lambda cid, text: (_ for _ in ()).throw(ConnectionError("whatsapp down"))
    cid = agent.start()
    agent.handle(cid, ["quero falar com alguém"])
    state = agent.store.load_state(cid)
    assert state.handoff.reason is HandoffReason.PEDIDO_HUMANO
    statuses = [e["status"] for e in agent.store.timeline(cid) if e["type"] == "message_out"]
    assert statuses == ["delivery_failed"]


def test_deferred_survives_a_crashing_client_and_cleans_up():
    class Exploding:
        def quote(self, request, **kwargs):
            raise RuntimeError("bug")

    done = []
    deferred = DeferredQuotes(Exploding(), schedule_s=(0.01, 0.01))
    request = HAPPY_REQUEST()
    deferred.submit(request, quote_id="q_x", conversation_id="c", on_done=done.append)
    deferred.wait(5)
    assert done[0].status is QuoteStatus.UNAVAILABLE
    assert deferred.pending() == []  # the finished thread is not kept around


def HAPPY_REQUEST():
    from autoseguro.quote import QuoteRequest

    return QuoteRequest(
        plano_id="essencial", idade=40, veiculo_ano=2020, cep="01310-100", data_inicio=START
    )


@pytest.mark.parametrize(
    ("text", "unbacked"),
    [
        ("fica uns 219 por mês", ["219 por mês"]),
        ("sai por 219,9 reais", ["219,9"]),
        ("franquia de R$ 4500", []),  # a real franquia, without thousand separator
        ("franquia de R$ 4.500,00", []),
        ("começa dia 15/11/2026, com carência de 30 dias", []),
    ],
)
def test_money_forms(text, unbacked):
    assert unbacked_amounts(text, {4500.0, 1500.0, 3000.0}) == unbacked


def test_quote_reference_ids_are_not_masked():
    text = "Ref.: q_ab12345678cd, q_1234567890ab"
    assert check_outbound(text, set()).text == text


def test_breaker_ignores_a_late_success_while_open():
    clock = [0.0]
    breaker = CircuitBreaker(min_calls=2, reset_timeout_s=10, clock=lambda: clock[0])
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "open"
    breaker.record_success()  # a slow call that started before the circuit opened
    assert breaker.state == "open" and not breaker.allow_request()


def test_quote_result_type_is_unchanged_for_mock_transport():
    # sanity: the client still talks to a real httpx transport after the changes
    from autoseguro.quote import QuoteClient

    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"premio_mensal": 1.0, "plano_id": "essencial"})

    client = QuoteClient("http://t", transport=httpx.MockTransport(ok), rng=random.Random(0))
    assert isinstance(client.quote(HAPPY_REQUEST()), QuoteResult)
