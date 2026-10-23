"""Bronze -> Silver: the conversations dataset, ordered, masked and parsed.

Bronze is `dataset/conversations.parquet` as delivered, with raw personal data. It is read,
never copied. Silver has two tables and no raw personal data:

- messages: one row per message, ordered by `message_index` (timestamps are unreliable),
  with the body masked;
- conversations: one row per conversation, with the fields the analysis needs (vehicle,
  age, CEP prefix, the seller's offer, objections, media, which kinds of PII appeared).

CPF, email, phone, plate and the lead's name become pseudonyms: a keyed HMAC, so the same
CPF yields the same token everywhere (still linkable for analysis) but can't be reversed
without the key. The CEP keeps only its first two digits, which is all the pricing rule uses.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import unicodedata
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from autoseguro.br_formats import normalize_cep
from autoseguro.privacy.redact import PiiKind, PiiMatch, find_pii, mask

BRONZE_PATH = Path("dataset/conversations.parquet")
SILVER_DIR = Path("var/data/silver")

OFFER_RE = re.compile(r"plano (?P<plano>\w+) por R\$ (?P<reais>\d+),(?P<centavos>\d{2})")
VEHICLE_RE = re.compile(r"^(?P<marca>\S+)\s+(?P<modelo>.+?)\s+(?P<ano>\d{4})$")

# Objection phrases seen in the dataset, grouped by what a seller would need to do about them.
OBJECTIONS = {
    "preco": ("preco ta salgado", "achei caro"),
    "franquia": ("franquia ta alta",),
    "concorrente": ("vi mais barato",),
    "pensar": ("preciso pensar",),
    "consultar_terceiro": ("vou ver com minha esposa",),
}


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def pseudonymizer(key: bytes) -> Callable[[PiiMatch], str]:
    def replace(m: PiiMatch) -> str:
        if m.kind is PiiKind.CEP:
            cep = normalize_cep(m.value)
            return f"[CEP:{cep[:2]}***]" if cep else "[CEP]"
        if m.kind in (PiiKind.CPF, PiiKind.TELEFONE):
            canonical = re.sub(r"\D", "", m.value)
        else:
            canonical = _fold(m.value)
        digest = hmac.new(key, f"{m.kind}:{canonical}".encode(), hashlib.sha256).hexdigest()
        return f"[{m.kind.upper()}:{digest[:8]}]"

    return replace


def _objection(lead_texts: list[str]) -> str | None:
    for text in lead_texts:
        folded = _fold(text)
        for category, phrases in OBJECTIONS.items():
            if any(p in folded for p in phrases):
                return category
    return None


def build_silver(bronze: pd.DataFrame, key: bytes) -> tuple[pd.DataFrame, pd.DataFrame]:
    replace = pseudonymizer(key)
    message_rows, conversation_rows = [], []
    ordered = bronze.sort_values(["conversation_id", "message_index"])
    for conversation_id, conv in ordered.groupby("conversation_id", sort=True):
        lead_name = conv.loc[conv.sender_role == "lead", "sender_name"].iloc[0]
        lead_token = replace(PiiMatch(PiiKind.NOME, lead_name, 0, len(lead_name)))
        timestamps = pd.to_datetime(conv.timestamp)
        out_of_order = (timestamps.diff().dt.total_seconds() < 0).tolist()

        pii_kinds: set[str] = set()
        cep_prefix = None
        offer = None
        lead_after_offer: list[str] = []
        for row, ts_out_of_order in zip(conv.itertuples(), out_of_order, strict=True):
            matches = find_pii(row.message_body, names=[lead_name])
            pii_kinds.update(m.kind.value for m in matches)
            if cep_prefix is None:
                cep = next((normalize_cep(m.value) for m in matches if m.kind is PiiKind.CEP), None)
                cep_prefix = cep[:2] if cep else None
            if row.sender_role == "vendedor" and offer is None:
                found = OFFER_RE.search(row.message_body)
                if found:
                    offer = (found["plano"].lower(), float(f"{found['reais']}.{found['centavos']}"))
            elif row.sender_role == "lead" and offer is not None:
                lead_after_offer.append(row.message_body)
            message_rows.append(
                {
                    "conversation_id": conversation_id,
                    "message_index": row.message_index,
                    "timestamp": row.timestamp,
                    "timestamp_fora_de_ordem": ts_out_of_order,
                    "sender_role": row.sender_role,
                    "sender": lead_token if row.sender_role == "lead" else row.sender_name,
                    "message_type": row.message_type,
                    "body": mask(row.message_body, matches, replace),
                    "pii_encontrada": sorted({m.kind.value for m in matches}),
                }
            )

        first = conv.iloc[0]
        vehicle = VEHICLE_RE.match(first.veiculo_texto)
        media = conv.message_type.value_counts()
        conversation_rows.append(
            {
                "conversation_id": conversation_id,
                "lead": lead_token,
                "vendedor": conv.loc[conv.sender_role == "vendedor", "sender_name"].iloc[0],
                "outcome": first.conversation_outcome,
                "idade": int(first.lead_idade_informada),
                "veiculo_marca": vehicle["marca"] if vehicle else None,
                "veiculo_modelo": vehicle["modelo"] if vehicle else None,
                "veiculo_ano": int(vehicle["ano"]) if vehicle else None,
                "cep_prefixo": cep_prefix,
                "oferta_plano": offer[0] if offer else None,
                "oferta_preco": offer[1] if offer else None,
                "objecao": _objection(lead_after_offer),
                "mencionou_concorrente": any("ofereceu menos" in t for t in lead_after_offer),
                "n_mensagens": len(conv),
                "n_documento": int(media.get("document", 0)),
                "n_imagem": int(media.get("image", 0)),
                "n_audio": int(media.get("audio", 0)),
                "pii_encontrada": sorted(pii_kinds),
                "timestamps_fora_de_ordem": any(out_of_order),
            }
        )
    return pd.DataFrame(message_rows), pd.DataFrame(conversation_rows)


def residual_pii(messages: pd.DataFrame, bronze: pd.DataFrame) -> int:
    """Count masked bodies where personal data is still detectable (should be zero)."""
    tokens = re.compile(r"\[[A-Z]+(?::[0-9a-f*]+)?\]")
    names = bronze.loc[bronze.sender_role == "lead", "sender_name"].unique().tolist()
    leftovers = 0
    for body in messages.body:
        if find_pii(tokens.sub(" ", body), names=names):
            leftovers += 1
    return leftovers


def run(bronze_path: Path = BRONZE_PATH, out_dir: Path = SILVER_DIR) -> dict[str, object]:
    key = os.environ.get("PII_HASH_KEY", "").encode()
    if not key:
        raise SystemExit("Defina PII_HASH_KEY no .env (chave do HMAC de pseudonimização).")
    bronze = pd.read_parquet(bronze_path)
    messages, conversations = build_silver(bronze, key)
    out_dir.mkdir(parents=True, exist_ok=True)
    messages.to_parquet(out_dir / "messages.parquet", index=False)
    conversations.to_parquet(out_dir / "conversations.parquet", index=False)
    summary = {
        "mensagens": len(messages),
        "conversas": len(conversations),
        "pii_residual": residual_pii(messages, bronze),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return {**summary, "saida": str(out_dir)}
