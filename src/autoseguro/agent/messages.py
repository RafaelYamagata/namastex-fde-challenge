"""Fixed texts: quote presentation, handoff notices, progress and fallback messages.

Every message that carries a price is built here from the quote API's JSON, never written
by the LLM. Handoff notices are fixed too: what the lead is told at that moment should not
depend on a model's mood.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from autoseguro.agent.state import HandoffReason, LeadData, QuoteRecord
from autoseguro.br_formats import format_brl
from autoseguro.quote.catalog import PlanCatalog

COVERAGE_LABELS = {
    "colisao": "colisão",
    "roubo": "roubo",
    "furto": "furto",
    "terceiros": "danos a terceiros",
    "vidros": "vidros",
    "carro_reserva": "carro reserva",
    "assistencia_24h": "assistência 24h",
}
MONTHS = [
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
]

PROGRESS = "Só um instante, estou calculando sua cotação no sistema... ⏳"

HANDOFF_MESSAGES = {
    HandoffReason.PEDIDO_HUMANO: (
        "Claro! Vou te passar para um dos nossos especialistas. Ele continua o atendimento "
        "por aqui mesmo, em instantes."
    ),
    HandoffReason.FECHAMENTO: (
        "Ótima escolha! 🎉 Para emitir a apólice, um especialista vai finalizar com você por "
        "aqui: ele confirma seus dados e envia a forma de pagamento."
    ),
    HandoffReason.NEGOCIACAO: (
        "Entendo. Vou chamar um especialista que pode avaliar outras condições pra você. Ele "
        "te responde por aqui."
    ),
    HandoffReason.COTACAO_INDISPONIVEL: (
        "Nosso sistema de cotação continua instável e não quero te deixar esperando. Passei "
        "seu pedido para um especialista, que te envia os valores por aqui."
    ),
    HandoffReason.INELEGIVEL: (
        "Para o seu perfil, a cotação é feita por um especialista da nossa equipe. Já passei "
        "seus dados e ele te chama por aqui em breve."
    ),
    HandoffReason.FORA_DE_ESCOPO: (
        "Esse assunto é tratado por outro time. Já encaminhei para um especialista, que te "
        "responde por aqui."
    ),
    HandoffReason.SEM_PROGRESSO: (
        "Pra não te fazer perder tempo, vou chamar um especialista para continuar com você "
        "por aqui."
    ),
    HandoffReason.ATRITO: (
        "Sinto muito por isso. Vou passar agora para um especialista da equipe, que vai te "
        "atender por aqui."
    ),
    HandoffReason.ERRO_INTERNO: (
        "Tive um problema técnico aqui. Para não te deixar sem resposta, já chamei um "
        "especialista, que continua o atendimento por aqui."
    ),
}

HANDOFF_ACK = "Um especialista já vai te responder por aqui. 🙂"

FIELD_LABELS = {
    "veiculo_ano": "o modelo e o ano do carro",
    "idade": "a idade do motorista principal",
    "cep": "o CEP de onde o carro dorme",
    "data_inicio": "a partir de quando você quer o seguro",
}


def join_pt(items: list[str]) -> str:
    """["a", "b", "c"] -> "a, b e c"."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " e " + items[-1]


def ask_fields_fallback(fields: list[str]) -> str:
    """Used when the LLM is unavailable or its text was blocked."""
    return f"Pra eu calcular sua cotação, me conta {join_pt([FIELD_LABELS[f] for f in fields])}?"


GENERIC_FALLBACK = "Entendi! Posso te ajudar com mais alguma coisa sobre a cotação?"


def _date_br(d: dt.date) -> str:
    return d.strftime("%d/%m/%Y")


def plan_block(data: dict[str, Any]) -> str:
    coberturas = join_pt([COVERAGE_LABELS.get(c, c) for c in data["coberturas"]])
    lines = [
        f"*{data['plano_nome']}*: {format_brl(data['premio_mensal'])}/mês",
        f"Coberturas: {coberturas}",
        f"Franquia: {format_brl(data['franquia'])}",
    ]
    pro_rata = data.get("primeiro_pagamento_pro_rata")
    if pro_rata:
        lines.append(
            f"1º pagamento: {format_brl(pro_rata['valor_primeiro_pagamento'])} "
            f"(proporcional a {pro_rata['dias_cobrados']} de {pro_rata['dias_no_mes']} dias)"
        )
    return "\n".join(lines)


def present_quotes(quotes: list[QuoteRecord], lead: LeadData, *, intro: str | None = None) -> str:
    """One WhatsApp message with every quote, the conditions and the references."""
    blocks = [plan_block(q.data) for q in quotes if q.data]
    first = quotes[0].data or {}
    notes = []
    if lead.data_inicio:
        notes.append(f"Início da vigência: {_date_br(lead.data_inicio)}.")
        if first.get("primeiro_pagamento_pro_rata"):
            month = MONTHS[lead.data_inicio.month - 1]
            notes.append(
                f"Como começa no meio do mês, o 1º pagamento cobre só os dias restantes de "
                f"{month}; depois, mensalidade cheia."
            )
    carencia = first.get("carencia") or {}
    if carencia.get("coberturas"):
        covered = join_pt([COVERAGE_LABELS.get(c, c) for c in carencia["coberturas"]])
        notes.append(
            f"Carência: {covered} passam a valer {carencia['dias']} dias após o início da vigência."
        )
    refs = ", ".join(q.quote_id for q in quotes)
    header = intro or ("Sua cotação:" if len(quotes) == 1 else "Suas cotações:")
    closing = (
        "Quer seguir com esse plano?"
        if len(quotes) == 1
        else "Qual deles faz mais sentido pra você? Posso explicar as diferenças."
    )
    return "\n\n".join([header, *blocks, " ".join(notes), closing, f"Ref.: {refs}"])


def deferred_notice(pending_plan_names: list[str], *, some_presented: bool) -> str:
    what = "a cotação" if not some_presented else f"a cotação do {join_pt(pending_plan_names)}"
    return (
        f"Nosso sistema de cotação está instável agora e não consegui calcular {what}. Não vou "
        "te passar um valor aproximado pra não te dar informação errada: assim que o sistema "
        "responder, te mando o valor certinho aqui."
    )


def plan_facts(catalog: PlanCatalog) -> str:
    """The facts the LLM may use when talking about plans. No prices on purpose."""
    lines = []
    for p in catalog.plans:
        coberturas = join_pt([COVERAGE_LABELS.get(c, c) for c in p["coberturas"]])
        lines.append(f"- {p['nome']}: {coberturas}; franquia de {format_brl(p['franquia'])}.")
    car = catalog.rules["carencia"]
    covered = join_pt([COVERAGE_LABELS.get(c, c) for c in car["coberturas_com_carencia"]])
    lines += [
        f"- Carência: {covered} só passam a valer {car['dias']} dias após o início da vigência.",
        "- Pagamento mensal. Se a vigência começa no meio do mês, o primeiro pagamento é "
        "proporcional aos dias restantes do mês; os seguintes são integrais.",
        "- O preço depende da idade do motorista, da idade do veículo e da região do CEP.",
        "- Franquia é o valor que o segurado paga em caso de sinistro com danos ao próprio "
        "carro; quanto menor a franquia, maior a mensalidade.",
        "- Para contratar, um especialista finaliza a emissão: confirma os dados e envia o "
        "pagamento.",
    ]
    return "\n".join(lines)
