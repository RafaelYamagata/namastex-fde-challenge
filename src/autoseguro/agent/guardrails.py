"""Checks on every outbound message, whoever wrote it.

1. Prices: every money amount must be a value the quote API returned in this conversation
   (monthly premium or prorated first payment) or a franquia from the plan table. Messages
   with prices are built from templates, so this should never fire for them; it exists for
   the LLM-written ones, as the second line of defense behind the prompt.
2. Personal data: nothing that looks like a CPF, phone, email or plate goes out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from autoseguro.agent.state import ConversationState
from autoseguro.privacy.redact import PiiKind, find_pii, mask
from autoseguro.quote.catalog import PlanCatalog

# Ways an amount can be written: "R$ 1.234,56", "R$ 4500", "219,90" or "219,9" without the
# symbol, "219 reais", "219 por mês" / "219/mês".
_NUMBER = r"(\d+(?:\.\d{3})*(?:,\d{1,2})?)"
MONEY_PATTERNS = [
    re.compile(rf"R\$\s*{_NUMBER}"),
    re.compile(r"(?<![\w.,])(\d+(?:\.\d{3})*,\d{1,2})(?![\w,])"),
    re.compile(rf"(?<![\w.,]){_NUMBER}\s*(?:reais|real)\b", re.IGNORECASE),
    re.compile(
        rf"(?<![\w.,]){_NUMBER}\s*(?:/\s*m[eê]s|por\s+m[eê]s|ao\s+m[eê]s|mensais)", re.IGNORECASE
    ),
]


def _to_float(text: str) -> float:
    return float(text.replace(".", "").replace(",", "."))


def allowed_amounts(state: ConversationState, catalog: PlanCatalog) -> set[float]:
    amounts = {float(p["franquia"]) for p in catalog.plans}
    for q in state.quotes.values():
        if q.status == "ok" and q.data:
            amounts.add(round(q.data["premio_mensal"], 2))
            amounts.add(round(float(q.data["franquia"]), 2))
            pro_rata = q.data.get("primeiro_pagamento_pro_rata")
            if pro_rata:
                amounts.add(round(pro_rata["valor_primeiro_pagamento"], 2))
    return amounts


def unbacked_amounts(text: str, allowed: set[float]) -> list[str]:
    bad, seen = [], set()
    for pattern in MONEY_PATTERNS:
        for m in pattern.finditer(text):
            if m.start(1) in seen:  # the same number caught by two patterns
                continue
            seen.add(m.start(1))
            if not any(abs(_to_float(m.group(1)) - a) < 0.005 for a in allowed):
                bad.append(m.group(0))
    return bad


@dataclass
class OutboundCheck:
    text: str
    unbacked: list[str]
    pii_masked: list[str]


def check_outbound(text: str, allowed: set[float]) -> OutboundCheck:
    unbacked = unbacked_amounts(text, allowed)
    pii = [m for m in find_pii(text) if m.kind is not PiiKind.NOME]
    return OutboundCheck(mask(text, pii) if pii else text, unbacked, [m.kind.value for m in pii])
