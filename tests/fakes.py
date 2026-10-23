"""Test doubles shared by the agent tests: a scripted LLM and a fake quote API."""

from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path

from autoseguro.agent.llm import LLMError
from autoseguro.agent.orchestrator import Agent
from autoseguro.clock import today as business_today
from autoseguro.quote import DeferredQuotes, PlanCatalog, QuoteResult, QuoteStatus
from autoseguro.store import Store

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

    def __init__(
        self,
        catalog: PlanCatalog,
        script: list[QuoteStatus] | None = None,
        unavailable_plans: set[str] | None = None,
    ):
        self.catalog = catalog
        self.script = list(script or [])
        self.unavailable_plans = unavailable_plans or set()
        self.calls = 0
        self._lock = threading.Lock()

    def quote(self, request, *, quote_id=None, conversation_id=None, round_no=0):
        with self._lock:
            self.calls += 1
            status = self.script.pop(0) if self.script else QuoteStatus.OK
            if request.plano_id in self.unavailable_plans:
                status = QuoteStatus.UNAVAILABLE
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


def catalog_snapshot() -> PlanCatalog:
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
