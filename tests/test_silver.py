from __future__ import annotations

import re

import pandas as pd

from autoseguro.data.silver import build_silver, pseudonymizer
from autoseguro.privacy.redact import PiiKind, PiiMatch

KEY = b"test-key"


def _sample() -> pd.DataFrame:
    return pd.read_json("dataset/sample.jsonl", lines=True, dtype={"timestamp": str})


def test_silver_has_no_raw_personal_data():
    bronze = _sample()
    messages, conversations = build_silver(bronze, KEY)
    raw_values = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}|@|\+55|\d{5}-\d{3}")
    assert not messages.body.str.contains(raw_values).any()
    for name in bronze.loc[bronze.sender_role == "lead", "sender_name"].unique():
        first_name = name.split()[0]
        assert not messages.body.str.contains(first_name).any()
        assert first_name not in conversations.lead.str.cat()


def test_silver_parses_offer_vehicle_and_cep_prefix():
    _, conversations = build_silver(_sample(), KEY)
    first = conversations.iloc[0]
    assert (first.oferta_plano, first.oferta_preco) == ("premium", 219.9)
    assert (first.veiculo_marca, first.veiculo_modelo, first.veiculo_ano) == (
        "Toyota",
        "Corolla",
        2008,
    )
    assert first.cep_prefixo == "26"


def test_pseudonyms_are_stable_and_keyed():
    cpf_a = PiiMatch(PiiKind.CPF, "389.083.863-43", 0, 14)
    cpf_b = PiiMatch(PiiKind.CPF, "38908386343", 0, 11)
    replace = pseudonymizer(KEY)
    assert replace(cpf_a) == replace(cpf_b)  # same CPF, different formatting
    assert replace(cpf_a) != pseudonymizer(b"other-key")(cpf_a)
    assert pseudonymizer(KEY)(PiiMatch(PiiKind.CEP, "07123-456", 0, 9)) == "[CEP:07***]"
