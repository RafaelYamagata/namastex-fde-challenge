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

# "R$ 1.234,56", "R$ 219,90", "R$219", and bare "219,90" (a price without the symbol).
MONEY_RE = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)|\b(\d+,\d{2})\b")


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
    bad = []
    for m in MONEY_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        if not any(abs(_to_float(raw) - a) < 0.005 for a in allowed):
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
