"""Find and mask personal data in free text (Brazilian formats).

Used in two places:
- at runtime, on every inbound message, before it reaches the LLM, the database or the logs;
- offline, to build the masked (Silver) layer of the conversations dataset.

Detection is regex-based on purpose: the formats are well defined (CPF, CEP, phone, plate,
email), the behavior is predictable and testable, and nothing leaves the machine to be
classified. Names can't be found by pattern, so callers pass the names they know (the
WhatsApp profile name, or `sender_name` in the dataset).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum

from autoseguro.br_formats import CEP_RE, cpf_is_valid


class PiiKind(StrEnum):
    CPF = "cpf"
    EMAIL = "email"
    TELEFONE = "telefone"
    PLACA = "placa"
    CEP = "cep"
    NOME = "nome"


@dataclass(frozen=True)
class PiiMatch:
    kind: PiiKind
    value: str
    start: int
    end: int


def _cpf_ok(value: str) -> bool:
    # With punctuation it's unambiguously a CPF. Eleven bare digits could be a phone number
    # with area code, so require valid check digits.
    return not value.isdigit() or cpf_is_valid(value)


# Order is priority: when two matches overlap, the earlier pattern wins.
_PATTERNS: list[tuple[PiiKind, re.Pattern[str], Callable[[str], bool] | None]] = [
    (PiiKind.EMAIL, re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), None),
    (PiiKind.CPF, re.compile(r"(?<!\d)\d{3}[.\s]?\d{3}[.\s]?\d{3}[-\s]?\d{2}(?!\d)"), _cpf_ok),
    (PiiKind.CEP, CEP_RE, None),
    (
        PiiKind.TELEFONE,
        re.compile(r"(?<!\d)(?:\+?55[\s.-]?)?(?:\(?\d{2}\)?[\s.-]?)?9?\d{4}[\s.-]?\d{4}(?!\d)"),
        None,
    ),
    # Old (ABC-1234) and Mercosul (ABC1D23) plates.
    (PiiKind.PLACA, re.compile(r"\b[A-Za-z]{3}-?\d[A-Za-z0-9]\d{2}\b"), None),
]


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _name_pattern(names: Iterable[str]) -> re.Pattern[str] | None:
    tokens: set[str] = set()
    for name in names:
        for token in re.findall(r"[^\W\d_]+", name):
            if len(token) >= 3:
                tokens.update({token, _strip_accents(token)})
    if not tokens:
        return None
    alternatives = "|".join(sorted((re.escape(t) for t in tokens), key=len, reverse=True))
    return re.compile(rf"\b(?:{alternatives})\b", re.IGNORECASE)


def find_pii(text: str, names: Iterable[str] = ()) -> list[PiiMatch]:
    accepted: list[PiiMatch] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < m.end and m.start < end for m in accepted)

    patterns = list(_PATTERNS)
    name_re = _name_pattern(names)
    if name_re is not None:
        patterns.append((PiiKind.NOME, name_re, None))
    for kind, pattern, validator in patterns:
        for m in pattern.finditer(text):
            if overlaps(m.start(), m.end()):
                continue
            if validator is not None and not validator(m.group(0)):
                continue
            accepted.append(PiiMatch(kind, m.group(0), m.start(), m.end()))
    return sorted(accepted, key=lambda m: m.start)


def default_token(match: PiiMatch) -> str:
    return f"[{match.kind.upper()}]"


def mask(
    text: str, matches: Iterable[PiiMatch], replace: Callable[[PiiMatch], str] = default_token
) -> str:
    out, cursor = [], 0
    for m in sorted(matches, key=lambda m: m.start):
        out.append(text[cursor : m.start])
        out.append(replace(m))
        cursor = m.end
    out.append(text[cursor:])
    return "".join(out)


@dataclass(frozen=True)
class Redaction:
    text: str
    matches: list[PiiMatch]

    def values(self, kind: PiiKind) -> list[str]:
        return [m.value for m in self.matches if m.kind is kind]


def redact(text: str, names: Iterable[str] = ()) -> Redaction:
    matches = find_pii(text, names)
    return Redaction(mask(text, matches), matches)
