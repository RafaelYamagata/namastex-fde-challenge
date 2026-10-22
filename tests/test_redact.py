from __future__ import annotations

import pytest

from autoseguro.privacy.redact import PiiKind, find_pii, redact


@pytest.mark.parametrize(
    ("text", "kind", "value"),
    [
        ("Tenho 35 anos, cep 26703-384, cpf 389.083.863-43", PiiKind.CPF, "389.083.863-43"),
        ("Tenho 35 anos, cep 26703-384, cpf 389.083.863-43", PiiKind.CEP, "26703-384"),
        ("meu cpf é 38908386343", PiiKind.CPF, "38908386343"),
        ("meu email é ursula.souza@gmail.com", PiiKind.EMAIL, "ursula.souza@gmail.com"),
        ("o whats é esse mesmo +55 21 97224-2584", PiiKind.TELEFONE, "+55 21 97224-2584"),
        ("liga no (11) 98765-4321", PiiKind.TELEFONE, "(11) 98765-4321"),
        ("liga no 11987654321", PiiKind.TELEFONE, "11987654321"),  # not a valid CPF
        ("a placa é ABC1D23 se precisar", PiiKind.PLACA, "ABC1D23"),
        ("placa antiga abc-1234", PiiKind.PLACA, "abc-1234"),
        ("cep 01310100", PiiKind.CEP, "01310100"),
    ],
)
def test_finds_each_kind(text, kind, value):
    assert (kind, value) in {(m.kind, m.value) for m in find_pii(text)}


@pytest.mark.parametrize(
    "text",
    [
        "tenho 35 anos",
        "é um Corolla 2008",
        "Pelo perfil consigo o plano Premium por R$ 219,90/mes",
        "franquia de R$ 3.000",
        "quero começar dia 15/11/2026",
    ],
)
def test_no_false_positives_on_quote_talk(text):
    assert find_pii(text) == []


def test_masks_everything_in_a_dataset_message():
    text = "Tenho 35 anos, cep 26703-384, cpf 389.083.863-43"
    assert redact(text).text == "Tenho 35 anos, cep [CEP], cpf [CPF]"


def test_names_including_the_seller_greeting():
    # The lead's name also leaks through the seller's messages.
    text = "Ola Ursula, tudo otimo! Claro, vou te ajudar."
    assert (
        redact(text, names=["Ursula Souza"]).text == "Ola [NOME], tudo otimo! Claro, vou te ajudar."
    )


def test_names_match_without_accents():
    assert redact("oi, aqui é o joao", names=["João Silva"]).text == "oi, aqui é o [NOME]"


def test_values_by_kind():
    r = redact("cep 01310-100 e cpf 389.083.863-43")
    assert r.values(PiiKind.CEP) == ["01310-100"]
