"""Plans and acceptance rules, read from GET /planos (published by the quote API itself).

The agent uses the rule table for two things only: describing plans (coverages, franquia,
carência; never prices) and checking eligibility as soon as age and vehicle year are known.
Prices always come from POST /quote.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

from autoseguro.clock import today as business_today
from autoseguro.obs import log_event

SNAPSHOT_PATH = Path(__file__).with_name("planos_snapshot.json")


class IneligibilityCode(StrEnum):
    IDADE_RECUSADA = "idade_recusada"  # a band that explicitly refuses (76+)
    IDADE_FORA_DAS_FAIXAS = "idade_fora_das_faixas"  # no band at all (under 18)
    VEICULO_RECUSADO = "veiculo_recusado"  # more than 20 years old
    VEICULO_FORA_DAS_FAIXAS = "veiculo_fora_das_faixas"  # next year's model: a gap in the rules


@dataclass(frozen=True)
class Ineligibility:
    code: IneligibilityCode
    motivo: str


class PlanCatalog:
    def __init__(self, data: dict[str, Any], *, source: str) -> None:
        self.data = data
        self.source = source  # "live" or "snapshot"

    @classmethod
    def load(
        cls, base_url: str, *, transport: httpx.BaseTransport | None = None, attempts: int = 3
    ) -> PlanCatalog:
        """Fetch the live table; fall back to the bundled snapshot if the API won't answer."""
        with httpx.Client(base_url=base_url, transport=transport, timeout=2.0) as http:
            for attempt in range(1, attempts + 1):
                try:
                    response = http.get("/planos")
                    response.raise_for_status()
                    return cls(response.json(), source="live")
                except (httpx.HTTPError, ValueError) as exc:
                    log_event("planos_fetch_failed", attempt=attempt, error=type(exc).__name__)
        log_event("planos_snapshot_used", path=str(SNAPSHOT_PATH))
        return cls.snapshot()

    @classmethod
    def snapshot(cls) -> PlanCatalog:
        """The table bundled with the agent (a copy of quote-service/data/plans.json)."""
        return cls(json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8")), source="snapshot")

    @property
    def plans(self) -> list[dict[str, Any]]:
        return self.data["planos"]

    @property
    def rules(self) -> dict[str, Any]:
        return self.data["regras"]

    def plan(self, plano_id: str) -> dict[str, Any] | None:
        return next((p for p in self.plans if p["id"] == plano_id), None)

    def check_age(self, idade: int) -> Ineligibility | None:
        for band in self.rules["faixa_etaria"]:
            if band["idade_min"] <= idade <= band["idade_max"]:
                if band.get("recusar"):
                    return Ineligibility(IneligibilityCode.IDADE_RECUSADA, band["motivo"])
                return None
        return Ineligibility(
            IneligibilityCode.IDADE_FORA_DAS_FAIXAS, "Idade fora das faixas aceitas."
        )

    def check_vehicle(self, veiculo_ano: int, today: dt.date | None = None) -> Ineligibility | None:
        # Same arithmetic as the API: only the calendar year counts.
        anos = (today or business_today()).year - veiculo_ano
        for band in self.rules["idade_veiculo"]:
            if band["anos_min"] <= anos <= band["anos_max"]:
                if band.get("recusar"):
                    return Ineligibility(IneligibilityCode.VEICULO_RECUSADO, band["motivo"])
                return None
        return Ineligibility(
            IneligibilityCode.VEICULO_FORA_DAS_FAIXAS, "Idade do veiculo fora das faixas aceitas."
        )
