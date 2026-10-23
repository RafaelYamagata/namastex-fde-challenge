from __future__ import annotations

import datetime as dt

from autoseguro.agent.guardrails import allowed_amounts, check_outbound, unbacked_amounts
from autoseguro.agent.nlu import parse
from autoseguro.agent.policy import alternative_plan
from autoseguro.agent.state import ConversationState, Intent, QuoteRecord
from autoseguro.quote import PlanCatalog

CATALOG = PlanCatalog.snapshot()


def _state_with_quote() -> ConversationState:
    state = ConversationState("conv_t")
    data = {
        "premio_mensal": 272.87,
        "franquia": 3000,
        "primeiro_pagamento_pro_rata": {"valor_primeiro_pagamento": 145.53},
    }
    state.quotes["q_1"] = QuoteRecord("q_1", "completo", "fp", "ok", data)
    return state


def test_amounts_from_quotes_and_franquias_are_allowed():
    allowed = allowed_amounts(_state_with_quote(), CATALOG)
    text = "Fica R$ 272,87/mês, 1º pagamento R$ 145,53 e franquia de R$ 3.000,00 (ou R$ 1.500)."
    assert unbacked_amounts(text, allowed) == []


def test_invented_amounts_are_caught_with_or_without_symbol():
    allowed = allowed_amounts(_state_with_quote(), CATALOG)
    assert unbacked_amounts("consigo por R$ 219,90 pra você", allowed) == ["R$ 219,90"]
    assert unbacked_amounts("sai por 199,90 no plano", allowed) == ["199,90"]
    assert unbacked_amounts("R$250", allowed) == ["R$250"]


def test_outbound_pii_is_masked():
    check = check_outbound("me liga no (11) 98765-4321", set())
    assert "98765" not in check.text and check.pii_masked == ["telefone"]


def test_alternatives():
    assert alternative_plan(Intent.OBJECAO_PRECO, ["completo"], CATALOG) == "essencial"
    assert alternative_plan(Intent.OBJECAO_PRECO, ["premium"], CATALOG) == "completo"
    assert alternative_plan(Intent.OBJECAO_PRECO, ["essencial"], CATALOG) is None
    assert alternative_plan(Intent.OBJECAO_FRANQUIA, ["essencial"], CATALOG) == "completo"
    assert alternative_plan(Intent.OBJECAO_FRANQUIA, ["premium"], CATALOG) is None


def test_nlu_parse_drops_implausible_values():
    today = dt.date(2026, 10, 22)
    raw = {
        "idade": 300,
        "veiculo_ano": 1890,
        "veiculo_modelo": " Onix ",
        "plano_id": "ouro",
        "data_inicio": "2026-01-01",
        "cep_invalido": False,
        "intencoes": ["duvida", "inventada"],
        "pergunta": "o que é franquia?",
    }
    result = parse(raw, today)
    assert (result.idade, result.veiculo_ano, result.plano_id, result.data_inicio) == (
        None,
        None,
        None,
        None,
    )
    assert result.veiculo_modelo == "Onix" and result.intents == {Intent.DUVIDA}
    assert len(result.dropped) == 4
