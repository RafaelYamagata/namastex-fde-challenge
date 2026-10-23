"""Conversation state: what the agent knows about one lead and where the conversation is.

The state is plain data, saved as JSON after every turn, so a conversation survives a
restart and every decision can be replayed from what was known at the time.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Stage(StrEnum):
    COLETANDO = "coletando"  # collecting what the quote needs
    CONFIRMANDO = (
        "confirmando"  # waiting for the lead to confirm a datum that makes them ineligible
    )
    AGUARDANDO_COTACAO = "aguardando_cotacao"  # a quote is being retried in the background
    COTADO = "cotado"  # at least one quote was presented
    HANDOFF = "handoff"  # a human owns the conversation now
    ENCERRADO = "encerrado"  # the lead gave up


class Intent(StrEnum):
    SAUDACAO = "saudacao"
    PEDIR_HUMANO = "pedir_humano"
    QUER_FECHAR = "quer_fechar"
    OBJECAO_PRECO = "objecao_preco"
    OBJECAO_FRANQUIA = "objecao_franquia"
    CONCORRENTE = "concorrente"
    VAI_PENSAR = "vai_pensar"
    DESISTIR = "desistir"
    DUVIDA = "duvida"
    OUTRO_PLANO = "outro_plano"
    REENVIAR_COTACAO = "reenviar_cotacao"
    CONFIRMA = "confirma"
    NEGA = "nega"
    FORA_DE_ESCOPO = "fora_de_escopo"
    RECLAMACAO = "reclamacao"


class HandoffReason(StrEnum):
    PEDIDO_HUMANO = "pedido_humano"
    FECHAMENTO = "fechamento"
    NEGOCIACAO = "negociacao"
    COTACAO_INDISPONIVEL = "cotacao_indisponivel"
    INELEGIVEL = "inelegivel"
    FORA_DE_ESCOPO = "fora_de_escopo"
    SEM_PROGRESSO = "sem_progresso"
    ATRITO = "atrito"
    ERRO_INTERNO = "erro_interno"


HANDOFF_PRIORITY = {
    HandoffReason.FECHAMENTO: "alta",
    HandoffReason.COTACAO_INDISPONIVEL: "alta",
    HandoffReason.ATRITO: "alta",
    HandoffReason.ERRO_INTERNO: "alta",
    HandoffReason.NEGOCIACAO: "normal",
    HandoffReason.PEDIDO_HUMANO: "normal",
    HandoffReason.INELEGIVEL: "normal",
    HandoffReason.FORA_DE_ESCOPO: "normal",
    HandoffReason.SEM_PROGRESSO: "baixa",
}

QUOTE_FIELDS = ("veiculo_ano", "idade", "cep", "data_inicio")


@dataclass
class LeadData:
    idade: int | None = None
    veiculo_ano: int | None = None
    veiculo_modelo: str | None = None
    cep: str | None = None  # full CEP: operational data, never logged (logs keep the prefix)
    plano_id: str | None = None  # None means "quote all plans"
    data_inicio: dt.date | None = None

    def missing_for_quote(self) -> list[str]:
        return [f for f in QUOTE_FIELDS if getattr(self, f) is None]

    def fingerprint(self) -> str:
        """Identifies the inputs of a quote; a quote is current only if this still matches."""
        inicio = self.data_inicio.isoformat() if self.data_inicio else None
        return f"{self.idade}|{self.veiculo_ano}|{self.cep}|{inicio}"


@dataclass
class QuoteRecord:
    quote_id: str
    plano_id: str
    fingerprint: str
    status: str  # pending | ok | refused | invalid | unavailable
    data: dict[str, Any] | None = None
    detail: str | None = None
    deferred: bool = False


@dataclass
class HandoffRecord:
    handoff_id: str
    reason: HandoffReason
    priority: str
    detail: str
    created_at: str


@dataclass
class ConversationState:
    conversation_id: str
    lead_name: str | None = None
    stage: Stage = Stage.COLETANDO
    data: LeadData = field(default_factory=LeadData)
    quotes: dict[str, QuoteRecord] = field(default_factory=dict)
    pending_confirmation: dict[str, Any] | None = None  # {"campo", "valor", "code", "motivo"}
    confirmed_ineligible: dict[str, Any] | None = None
    alternative_offered: bool = False
    unreadable_media: int = 0
    turns_without_progress: int = 0
    greeted: bool = False
    handoff: HandoffRecord | None = None
    handoff_acknowledged: bool = False
    outcome: str | None = None
    transcript: list[tuple[str, str]] = field(default_factory=list)  # (role, masked text)

    def current_quotes(self) -> dict[str, QuoteRecord]:
        """Successful quotes for the lead's current data, by plan."""
        fp = self.data.fingerprint()
        return {
            q.plano_id: q for q in self.quotes.values() if q.status == "ok" and q.fingerprint == fp
        }

    def pending_quotes(self) -> list[QuoteRecord]:
        return [q for q in self.quotes.values() if q.status == "pending"]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=_default)

    @classmethod
    def from_json(cls, raw: str) -> ConversationState:
        d = json.loads(raw)
        data = d.pop("data")
        if data.get("data_inicio"):
            data["data_inicio"] = dt.date.fromisoformat(data["data_inicio"])
        handoff = d.pop("handoff")
        return cls(
            **{k: v for k, v in d.items() if k not in ("quotes", "transcript", "stage")},
            stage=Stage(d["stage"]),
            data=LeadData(**data),
            quotes={k: QuoteRecord(**v) for k, v in d["quotes"].items()},
            handoff=HandoffRecord(**{**handoff, "reason": HandoffReason(handoff["reason"])})
            if handoff
            else None,
            transcript=[tuple(t) for t in d["transcript"]],
        )


def _default(value: object) -> object:
    if isinstance(value, dt.date):
        return value.isoformat()
    raise TypeError(f"not serializable: {type(value)}")
