"""Types for the quote API: a stricter request model and the outcome of one quote."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoseguro.br_formats import normalize_cep

PlanId = Literal["essencial", "completo", "premium"]
PLAN_IDS: tuple[PlanId, ...] = ("essencial", "completo", "premium")


class QuoteRequest(BaseModel):
    """Payload for POST /quote, validated more strictly than the API does.

    The API accepts inputs that silently produce a wrong price: without `cep` the high-risk
    region surcharge is skipped, a malformed CEP is read from its first two characters, a
    missing `plano_id` becomes "essencial", and a start date in the past is accepted. Here
    every field is required and normalized, so none of those paths can be reached.
    """

    model_config = ConfigDict(frozen=True)

    plano_id: PlanId
    idade: int = Field(ge=0, le=120)
    veiculo_ano: int = Field(ge=1950, le=2100)
    cep: str
    data_inicio: dt.date

    @field_validator("cep")
    @classmethod
    def _normalize_cep(cls, value: str) -> str:
        cep = normalize_cep(value)
        if cep is None:
            raise ValueError("CEP precisa ter 8 dígitos")
        return cep

    @field_validator("data_inicio")
    @classmethod
    def _not_in_past(cls, value: dt.date) -> dt.date:
        if value < dt.date.today():
            raise ValueError("data de início no passado")
        return value

    def payload(self) -> dict[str, Any]:
        return {**self.model_dump(), "data_inicio": self.data_inicio.isoformat()}


class QuoteStatus(StrEnum):
    OK = "ok"  # the API returned a price
    REFUSED = "refused"  # business refusal (422 cotacao_recusada); never retried
    INVALID = "invalid"  # the API rejected our payload (400, or 422 validation); a bug on our side
    UNAVAILABLE = "unavailable"  # retries exhausted or circuit open: there is no price


@dataclass(frozen=True)
class QuoteAttempt:
    number: int
    started_at: dt.datetime
    latency_ms: float
    status_code: int | None  # None when there was no HTTP response
    error: str | None  # "timeout", "connect_error", "http_503", "bad_body"...


@dataclass
class QuoteResult:
    quote_id: str
    request: QuoteRequest
    status: QuoteStatus
    data: dict[str, Any] | None = None  # API response body, only when status is OK
    detail: str | None = None  # refusal reason, validation message or last error
    attempts: list[QuoteAttempt] = field(default_factory=list)
    cached: bool = False
    circuit_open: bool = False
