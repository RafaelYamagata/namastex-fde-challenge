"""Brazilian formats: CEP, CPF check digits, money."""

from __future__ import annotations

import re

# 01310-100, 01310100, 01.310-100, 01310 100
CEP_RE = re.compile(r"(?<!\d)(\d{2})\.?(\d{3})[-\s]?(\d{3})(?!\d)")


def normalize_cep(value: str) -> str | None:
    """Return the CEP as "NNNNN-NNN", or None if `value` is not exactly one CEP.

    This matters for pricing: the quote API reads the region from the first two characters of
    whatever string it receives, so "CEP 07123-456" or a CEP that lost its leading zero
    ("7123-456") would silently be priced for the wrong region.
    """
    m = CEP_RE.fullmatch(value.strip())
    if m is None:
        return None
    a, b, c = m.groups()
    return f"{a}{b}-{c}"


def cpf_is_valid(value: str) -> bool:
    """Check the two CPF check digits (used to tell a CPF from an 11-digit phone number)."""
    d = [int(c) for c in re.sub(r"\D", "", value)]
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for n in (9, 10):
        s = sum(v * (n + 1 - i) for i, v in enumerate(d[:n]))
        if (s * 10) % 11 % 10 != d[n]:
            return False
    return True


def format_brl(value: float) -> str:
    """1234.5 -> "R$ 1.234,50"."""
    s = f"{value:,.2f}"
    return "R$ " + s.replace(",", "§").replace(".", ",").replace("§", ".")
