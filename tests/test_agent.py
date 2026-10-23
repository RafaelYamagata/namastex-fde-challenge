"""The agent end to end with a scripted LLM and a fake quote API (no network).

The fake LLM plays back NLU results in order and answers every NLG call with a fixed text,
recording every prompt, so the tests can also check what the LLM was (not) shown.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path

import pytest

from autoseguro.agent.llm import LLMError
from autoseguro.agent.orchestrator import Agent
from autoseguro.agent.state import HandoffReason, Stage
from autoseguro.clock import today as business_today
from autoseguro.quote import DeferredQuotes, PlanCatalog, QuoteResult, QuoteStatus
from autoseguro.store import Store
from autoseguro.trace import render_trace, render_transcript

TODAY = business_today()
START = (TODAY.replace(day=1) + dt.timedelta(days=32)).replace(day=15)


def nlu(**fields) -> dict:
    base = {
        "idade": None,
        "veiculo_ano": None,
        "veiculo_modelo": None,
        "plano_id": None,
        "data_inicio": None,
        "cep_invalido": False,
        "intencoes": [],
        "pergunta": None,
    }
    if isinstance(fields.get("data_inicio"), dt.date):
        fields["data_inicio"] = fields["data_inicio"].isoformat()
    return {**base, **fields}


class ScriptedLLM:
    def __init__(self, nlu_results: list[dict], nlg_text: str = "Certo! Pode me contar mais?"):
        self.nlu_results = list(nlu_results)
        self.nlg_texts: list[str] = []
        self.nlg_text = nlg_text
        self.prompts: list[str] = []
        self.fail_nlu = False

    def complete(self, *, system, user, purpose, conversation_id, schema=None):
        self.prompts.append(user)
        if purpose == "nlu":
            if self.fail_nlu:
                raise LLMError("APIConnectionError")
            return json.dumps(self.nlu_results.pop(0))
        return self.nlg_texts.pop(0) if self.nlg_texts else self.nlg_text


class FakeQuoteClient:
    """Prices with the plan's base price; `script` forces statuses per call, in order."""

    def __init__(self, catalog: PlanCatalog, script: list[QuoteStatus] | None = None):
        self.catalog = catalog
        self.script = list(script or [])
        self.calls = 0
        self._lock = threading.Lock()

    def quote(self, request, *, quote_id=None, conversation_id=None, round_no=0):
        with self._lock:
            self.calls += 1
            status = self.script.pop(0) if self.script else QuoteStatus.OK
        if status is not QuoteStatus.OK:
            return QuoteResult(quote_id=quote_id, request=request, status=status, detail="x")
        plan = self.catalog.plan(request.plano_id)
        data = {
            "plano_id": plan["id"],
            "plano_nome": plan["nome"],
            "premio_mensal": plan["base_mensal"],
            "franquia": plan["franquia"],
            "coberturas": plan["coberturas"],
            "carencia": {"coberturas": ["roubo", "furto"], "dias": 30, "observacao": ""},
            "moeda": "BRL",
            "primeiro_pagamento_pro_rata": {
                "dias_no_mes": 30,
                "dias_cobrados": 16,
                "valor_primeiro_pagamento": round(plan["base_mensal"] * 16 / 30, 2),
            },
        }
        return QuoteResult(quote_id=quote_id, request=request, status=status, data=data)


@pytest.fixture
def catalog() -> PlanCatalog:
    return PlanCatalog.snapshot()


def make_agent(tmp_path: Path, catalog, llm, client=None, schedule=(0.01,)):
    sent: list[str] = []
    client = client or FakeQuoteClient(catalog)
    agent = Agent(
        llm=llm,
        quote_client=client,
        catalog=catalog,
        store=Store(tmp_path / "t.db"),
        deferred=DeferredQuotes(client, schedule),
        send=lambda cid, text: sent.append(text),
    )
    return agent, sent


def decisions(agent: Agent, cid: str) -> list[str]:
    return [e["rule"] for e in agent.store.timeline(cid) if e["type"] == "decision"]


HAPPY = [
    nlu(intencoes=["saudacao"]),
    nlu(veiculo_ano=2021, veiculo_modelo="Onix"),
    nlu(idade=35, data_inicio=START),
]


def test_happy_path_collects_quotes_all_plans_and_closes(tmp_path, catalog):
    llm = ScriptedLLM(HAPPY + [nlu(intencoes=["quer_fechar"], plano_id="completo")])
    agent, sent = make_agent(tmp_path, catalog, llm)
    cid = agent.start(lead_name="Mariana Souza")

    agent.handle(cid, ["Oi, quero fazer um seguro pro meu carro"])
    agent.handle(cid, ["é um Onix 2021"])
    out = agent.handle(cid, ["tenho 35 anos, cep 01310-100, quero começar dia 15"])

    quote_msg = out[-1]
    assert "R$ 119,90/mês" in quote_msg and "R$ 209,90/mês" in quote_msg
    assert "Carência" in quote_msg and "1º pagamento" in quote_msg
    state = agent.store.load_state(cid)
    assert state.stage is Stage.COTADO and len(state.current_quotes()) == 3

    agent.handle(cid, ["fechado, quero o completo"])
    state = agent.store.load_state(cid)
    assert state.handoff.reason is HandoffReason.FECHAMENTO
    assert state.handoff.priority == "alta"
    assert decisions(agent, cid) == [
        "R18_coletar_dados",
        "R18_coletar_dados",
        "R19_cotar",
        "R04_fechamento",
    ]


def test_llm_never_sees_personal_data(tmp_path, catalog):
    llm = ScriptedLLM(HAPPY)
    agent, _ = make_agent(tmp_path, catalog, llm)
    cid = agent.start(lead_name="Mariana Souza")
    agent.handle(cid, ["Oi, aqui é a Mariana"])
    agent.handle(cid, ["Onix 2021, placa ABC1D23"])
    agent.handle(cid, ["35 anos, cep 01310-100, cpf 389.083.863-43, dia 15"])
    everything = "\n".join(llm.prompts)
    for raw in ("Mariana", "ABC1D23", "01310", "389.083.863-43"):
        assert raw not in everything
    stored = [e["body_masked"] for e in agent.store.timeline(cid) if e["type"] == "message_in"]
    assert "[CPF]" in stored[-1] and "389" not in stored[-1]


def test_invented_price_from_llm_is_blocked(tmp_path, catalog):
    llm = ScriptedLLM([nlu(intencoes=["saudacao"])], nlg_text="Fica só R$ 99,90 por mês!")
    agent, sent = make_agent(tmp_path, catalog, llm)
    cid = agent.start()
    out = agent.handle(cid, ["oi"])
    assert "99,90" not in out[0]
    assert "modelo e o ano do carro" in out[0]  # the fixed fallback for this directive
    blocked = [e for e in agent.store.timeline(cid) if e["type"] == "guardrail_blocked"]
    assert len(blocked) == 2  # first try + stricter retry


def test_unavailable_quote_is_deferred_then_delivered(tmp_path, catalog):
    client = FakeQuoteClient(catalog, [QuoteStatus.UNAVAILABLE] * 3)
    llm = ScriptedLLM(HAPPY)
    agent, sent = make_agent(tmp_path, catalog, llm, client)
    cid = agent.start()
    for m in ["oi", "Onix 2021", "35 anos, cep 01310-100, dia 15"]:
        out = agent.handle(cid, [m])
    assert "instável" in out[-1] and "R$" not in out[-1]
    assert agent.store.load_state(cid).stage is Stage.AGUARDANDO_COTACAO
    agent.deferred.wait(5)
    proactive = [s for s in sent if s.startswith("Saiu!")]
    assert len(proactive) == 3 and "R$" in proactive[0]
    assert agent.store.load_state(cid).stage is Stage.COTADO


def test_quote_that_never_comes_goes_to_a_human(tmp_path, catalog):
    client = FakeQuoteClient(catalog, [QuoteStatus.UNAVAILABLE] * 99)
    llm = ScriptedLLM(HAPPY[:2] + [nlu(idade=35, data_inicio=START, plano_id="essencial")])
    agent, sent = make_agent(tmp_path, catalog, llm, client, schedule=(0.01, 0.01))
    cid = agent.start()
    for m in ["oi", "Onix 2021", "35 anos, cep 01310-100, dia 15, essencial"]:
        agent.handle(cid, [m])
    agent.deferred.wait(5)
    state = agent.store.load_state(cid)
    assert state.handoff.reason is HandoffReason.COTACAO_INDISPONIVEL
    assert not any("R$" in s for s in sent)


def test_ineligible_age_is_confirmed_then_handed_off(tmp_path, catalog):
    llm = ScriptedLLM(
        [
            nlu(veiculo_ano=2020, idade=80),
            nlu(intencoes=["confirma"]),
            nlu(),  # the CEP comes in by regex
        ]
    )
    agent, _ = make_agent(tmp_path, catalog, llm)
    cid = agent.start()
    agent.handle(cid, ["tenho 80 anos e um HB20 2020"])
    assert agent.store.load_state(cid).stage is Stage.CONFIRMANDO
    agent.handle(cid, ["isso mesmo"])
    agent.handle(cid, ["01310-100"])
    state = agent.store.load_state(cid)
    assert state.handoff.reason is HandoffReason.INELEGIVEL
    assert decisions(agent, cid) == [
        "R13_confirmar_dado",
        "R14_dados_para_especialista",
        "R15_inelegivel",
    ]
    assert agent.store.timeline(cid)  # no quote was attempted:
    assert not [e for e in agent.store.timeline(cid) if e["type"] == "quote"]


def test_corrected_age_resumes_the_quote(tmp_path, catalog):
    llm = ScriptedLLM(
        [
            nlu(veiculo_ano=2020, idade=76),
            nlu(idade=67, intencoes=["nega"]),
        ]
    )
    agent, _ = make_agent(tmp_path, catalog, llm)
    cid = agent.start()
    agent.handle(cid, ["76 anos, HB20 2020"])
    agent.handle(cid, ["não, desculpa, tenho 67"])
    state = agent.store.load_state(cid)
    assert state.stage is Stage.COLETANDO and state.data.idade == 67
    assert decisions(agent, cid)[-1] == "R18_coletar_dados"


def test_objection_gets_one_alternative_then_a_human(tmp_path, catalog):
    llm = ScriptedLLM(
        HAPPY[:2]
        + [
            nlu(idade=35, data_inicio=START, plano_id="completo"),
            nlu(intencoes=["objecao_preco"]),
            nlu(intencoes=["concorrente"]),
        ]
    )
    agent, _ = make_agent(tmp_path, catalog, llm)
    cid = agent.start()
    for m in ["oi", "Onix 2021", "35, cep 01310-100, dia 15, quero o completo"]:
        agent.handle(cid, [m])
    out = agent.handle(cid, ["achei caro"])
    assert "Essencial" in out[-1]  # the cheaper plan, quoted and presented
    agent.handle(cid, ["a Porto me ofereceu menos"])
    state = agent.store.load_state(cid)
    assert state.handoff.reason is HandoffReason.NEGOCIACAO
    assert decisions(agent, cid)[-2:] == ["R06_objecao_alternativa", "R07_negociacao"]


def test_human_request_then_one_ack_then_silence(tmp_path, catalog):
    llm = ScriptedLLM([nlu(intencoes=["pedir_humano"])])
    agent, _ = make_agent(tmp_path, catalog, llm)
    cid = agent.start()
    assert "especialistas" in agent.handle(cid, ["quero falar com uma pessoa"])[0]
    assert agent.handle(cid, ["alô?"]) == ["Um especialista já vai te responder por aqui. 🙂"]
    assert agent.handle(cid, ["??"]) == []
    # the trace and the transcript render the handoff exactly once
    assert render_trace(agent.store, cid).count("HANDOFF") == 1
    assert render_transcript(agent.store, cid).count("Handoff") == 1


def test_media_twice_goes_to_a_human(tmp_path, catalog):
    agent, _ = make_agent(tmp_path, catalog, ScriptedLLM([]))
    cid = agent.start()
    agent.handle(cid, ["[audio] mensagem de voz (18s)"])
    agent.handle(cid, ["[imagem] foto do veiculo na garagem"])
    state = agent.store.load_state(cid)
    assert state.handoff.reason is HandoffReason.SEM_PROGRESSO
    assert decisions(agent, cid) == ["R11_midia_ilegivel", "R10_midia_repetida"]


def test_llm_outage_goes_to_a_human(tmp_path, catalog):
    llm = ScriptedLLM([])
    llm.fail_nlu = True
    agent, _ = make_agent(tmp_path, catalog, llm)
    cid = agent.start()
    out = agent.handle(cid, ["oi"])
    assert "problema técnico" in out[0]
    assert agent.store.load_state(cid).handoff.reason is HandoffReason.ERRO_INTERNO


def test_turns_without_progress_go_to_a_human(tmp_path, catalog):
    llm = ScriptedLLM([nlu(), nlu(), nlu()])
    agent, _ = make_agent(tmp_path, catalog, llm)
    cid = agent.start()
    for m in ["hmm", "sei la", "???"]:
        agent.handle(cid, [m])
    assert agent.store.load_state(cid).handoff.reason is HandoffReason.SEM_PROGRESSO


def test_state_survives_a_restart(tmp_path, catalog):
    llm = ScriptedLLM(HAPPY)
    agent, _ = make_agent(tmp_path, catalog, llm)
    cid = agent.start()
    for m in ["oi", "Onix 2021", "35 anos, cep 01310-100, dia 15"]:
        agent.handle(cid, [m])
    reloaded = agent.store.load_state(cid)
    assert reloaded.data.cep == "01310-100" and reloaded.data.data_inicio == START
    assert len(reloaded.current_quotes()) == 3
