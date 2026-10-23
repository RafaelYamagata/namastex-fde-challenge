"""The rules engine: given what the lead said, decide what the agent does next.

Pure Python, no LLM, no I/O, so every rule is unit-tested. Rules are checked in order and
the first one that matches decides the turn; its id (R01...) is recorded in the trace, so
"why did the agent do that?" always has a one-line answer.

`decide` also keeps the conversation's bookkeeping (stage, pending confirmations, counters);
the orchestrator executes the returned actions (talk, quote, present, hand off).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from autoseguro.agent.messages import FIELD_LABELS, join_pt
from autoseguro.agent.nlu import NluResult
from autoseguro.agent.state import ConversationState, HandoffReason, Intent, Stage
from autoseguro.br_formats import normalize_cep
from autoseguro.quote.catalog import PlanCatalog
from autoseguro.quote.models import PLAN_IDS

# How many turns without new information before a human takes over.
MAX_TURNS_WITHOUT_PROGRESS = 3


@dataclass
class Turn:
    """What arrived in one turn (a burst of WhatsApp messages), already masked."""

    texts: list[str] = field(default_factory=list)
    media: list[str] = field(default_factory=list)  # "audio" | "imagem" | "documento"
    ceps: list[str] = field(default_factory=list)  # captured by regex before masking


@dataclass
class Directive:
    """What the LLM should write: a short code (for the trace) and the instruction."""

    code: str
    instruction: str


@dataclass
class Decision:
    rule: str
    directive: Directive | None = None
    quote_plans: list[str] = field(default_factory=list)
    present_plans: list[str] = field(default_factory=list)
    handoff: HandoffReason | None = None
    handoff_detail: str = ""


def apply_nlu(state: ConversationState, nlu: NluResult, turn: Turn) -> list[str]:
    """Merge what the lead just said into the lead data; return the fields that changed."""
    changed = []
    for name in ("idade", "veiculo_ano", "veiculo_modelo", "plano_id", "data_inicio"):
        value = getattr(nlu, name)
        if value is not None and getattr(state.data, name) != value:
            setattr(state.data, name, value)
            changed.append(name)
    ceps = [c for c in (normalize_cep(v) for v in turn.ceps) if c]
    if ceps and ceps[-1] != state.data.cep:
        state.data.cep = ceps[-1]
        changed.append("cep")
    return changed


def check_ineligible(state: ConversationState, catalog: PlanCatalog, today: dt.date) -> dict | None:
    d = state.data
    if d.idade is not None and (found := catalog.check_age(d.idade)):
        return {
            "campo": "idade",
            "valor": d.idade,
            "code": found.code.value,
            "motivo": found.motivo,
        }
    if d.veiculo_ano is not None and (found := catalog.check_vehicle(d.veiculo_ano, today)):
        return {
            "campo": "veiculo_ano",
            "valor": d.veiculo_ano,
            "code": found.code.value,
            "motivo": found.motivo,
        }
    return None


def alternative_plan(objection: Intent, quoted: list[str], catalog: PlanCatalog) -> str | None:
    """A price objection gets the next cheaper plan; a franquia objection, the next plan with
    a lower franquia. None when the lead has already seen it."""
    plans = [catalog.plan(p) for p in quoted]
    if objection is Intent.OBJECAO_FRANQUIA:
        floor = min(p["franquia"] for p in plans)
        options = [p for p in catalog.plans if p["franquia"] < floor]
        best = max(options, key=lambda p: p["franquia"]) if options else None
    else:
        floor = min(p["base_mensal"] for p in plans)
        options = [p for p in catalog.plans if p["base_mensal"] < floor]
        best = max(options, key=lambda p: p["base_mensal"]) if options else None
    return best["id"] if best else None


def _same(pending: dict | None, found: dict) -> bool:
    return bool(pending) and (pending["campo"], pending["valor"]) == (
        found["campo"],
        found["valor"],
    )


def _ask(fields: list[str]) -> str:
    return join_pt([FIELD_LABELS[f] for f in fields])


def decide(
    state: ConversationState,
    nlu: NluResult,
    turn: Turn,
    changed: list[str],
    catalog: PlanCatalog,
    today: dt.date,
) -> Decision:
    intents = nlu.intents
    quotes = state.current_quotes()
    answer = (
        f" Antes, responda à dúvida do lead ({nlu.pergunta}) usando só os FATOS."
        if Intent.DUVIDA in intents and nlu.pergunta
        else ""
    )
    if turn.texts and {"audio", "imagem"} & set(turn.media):
        answer += " O lead também mandou áudio ou imagem: diga que por aqui você só lê texto."

    # --- Always a human, whatever the stage --------------------------------------------
    if Intent.PEDIR_HUMANO in intents:
        return Decision(
            "R01_pedido_humano",
            handoff=HandoffReason.PEDIDO_HUMANO,
            handoff_detail="Lead pediu para falar com uma pessoa.",
        )
    if Intent.RECLAMACAO in intents:
        return Decision(
            "R02_reclamacao",
            handoff=HandoffReason.ATRITO,
            handoff_detail="Reclamação ou irritação do lead.",
        )
    if Intent.FORA_DE_ESCOPO in intents:
        return Decision(
            "R03_fora_de_escopo",
            handoff=HandoffReason.FORA_DE_ESCOPO,
            handoff_detail=nlu.pergunta or "Assunto fora de cotação de seguro auto.",
        )

    # --- After a quote: closing, objections, giving up -------------------------------
    if quotes and (Intent.QUER_FECHAR in intents or "documento" in turn.media):
        plan = (
            nlu.plano_id
            or state.data.plano_id
            or (next(iter(quotes)) if len(quotes) == 1 else None)
        )
        refs = ", ".join(q.quote_id for q in quotes.values())
        detail = f"Lead quer fechar{f' o plano {plan}' if plan else ''}. Cotações: {refs}."
        state.outcome = "fechamento"
        return Decision("R04_fechamento", handoff=HandoffReason.FECHAMENTO, handoff_detail=detail)

    if Intent.DESISTIR in intents:
        state.stage, state.outcome = Stage.ENCERRADO, "perdido"
        return Decision(
            "R05_desistiu",
            Directive(
                "despedida",
                "O lead não quer seguir. Agradeça, sem insistir, e diga que pode "
                "chamar quando quiser.",
            ),
        )

    objection = next(
        (
            i
            for i in (Intent.OBJECAO_PRECO, Intent.CONCORRENTE, Intent.OBJECAO_FRANQUIA)
            if i in intents
        ),
        None,
    )
    if quotes and objection:
        alt = (
            None
            if state.alternative_offered
            else alternative_plan(objection, list(quotes), catalog)
        )
        if alt:
            state.alternative_offered = True
            alt_plan = catalog.plan(alt)
            why = "franquia menor" if objection is Intent.OBJECAO_FRANQUIA else "mensalidade menor"
            return Decision(
                "R06_objecao_alternativa",
                Directive(
                    "alternativa",
                    f"O lead fez uma objeção ({objection.value}). "
                    "Reconheça sem prometer desconto e diga que vai mostrar o plano "
                    f"{alt_plan['nome']} como alternativa, com {why}; explique a troca "
                    "(coberturas e franquia) usando só os FATOS.",
                ),
                quote_plans=[alt],
            )
        state.outcome = "negociacao"
        seen = (
            "depois de ver uma alternativa"
            if state.alternative_offered
            else "sem alternativa possível"
        )
        return Decision(
            "R07_negociacao",
            handoff=HandoffReason.NEGOCIACAO,
            handoff_detail=f"Objeção ({objection.value}) {seen}.",
        )

    if quotes and Intent.VAI_PENSAR in intents:
        state.outcome = "em_negociacao"
        return Decision(
            "R08_vai_pensar",
            Directive(
                "vai_pensar",
                "O lead vai pensar. Diga que a cotação fica registrada, que os "
                "valores podem mudar com o tempo e que é só chamar aqui quando quiser.",
            ),
        )

    # --- Media the agent can't read --------------------------------------------------
    if turn.media and not turn.texts:
        if "documento" in turn.media:
            missing = state.data.missing_for_quote()
            ask = f" Peça {_ask(missing)}." if missing else ""
            return Decision(
                "R09_documento_antes_da_cotacao",
                Directive(
                    "documento",
                    "O lead mandou um documento. Diga que para cotar não precisa de "
                    f"documento, só de alguns dados.{ask}",
                ),
            )
        if state.unreadable_media >= 1:
            return Decision(
                "R10_midia_repetida",
                handoff=HandoffReason.SEM_PROGRESSO,
                handoff_detail="Lead segue mandando áudio/imagem, que o agente não lê.",
            )
        state.unreadable_media += 1
        return Decision(
            "R11_midia_ilegivel",
            Directive(
                "midia",
                "O lead mandou áudio ou imagem. Explique que por aqui você só consegue "
                "ler texto e peça para ele escrever.",
            ),
        )

    # --- Eligibility: the rules refuse this profile ------------------------------------
    found = check_ineligible(state, catalog, today)
    if found:
        confirmed = _same(state.confirmed_ineligible, found)
        if not confirmed and _same(state.pending_confirmation, found):
            restated = getattr(nlu, found["campo"]) == found["valor"]
            if Intent.CONFIRMA in intents or restated:
                state.confirmed_ineligible = {**found, "pediu_faltantes": False}
                state.pending_confirmation = None
                confirmed = True
            elif Intent.NEGA in intents:
                label = "a idade" if found["campo"] == "idade" else "o ano do carro"
                return Decision(
                    "R12_pedir_correcao",
                    Directive(
                        "corrigir", f"O lead disse que o dado está errado. Peça {label} correto."
                    ),
                )
        if not confirmed:
            state.stage, state.pending_confirmation = Stage.CONFIRMANDO, found
            label = "a idade do motorista" if found["campo"] == "idade" else "o ano do veículo"
            return Decision(
                "R13_confirmar_dado",
                Directive(
                    "confirmar",
                    f"Confirme com o lead {label}: {found['valor']}. É só uma "
                    "confirmação; não fale em recusa nem em regras.",
                ),
            )
        missing = [f for f in ("veiculo_ano", "idade", "cep") if getattr(state.data, f) is None]
        if missing and not state.confirmed_ineligible["pediu_faltantes"]:
            state.confirmed_ineligible["pediu_faltantes"] = True
            return Decision(
                "R14_dados_para_especialista",
                Directive(
                    "dados_especialista",
                    f"Peça {_ask(missing)}, para seguir com a cotação. "
                    "Não fale em recusa nem em regras.",
                ),
            )
        state.outcome = "inelegivel"
        return Decision(
            "R15_inelegivel",
            handoff=HandoffReason.INELEGIVEL,
            handoff_detail=f"{found['code']}: {found['motivo']} "
            f"({found['campo']} = {found['valor']}).",
        )
    if state.stage is Stage.CONFIRMANDO:  # the lead corrected the datum
        state.stage, state.pending_confirmation = Stage.COLETANDO, None

    # --- Collecting what the quote needs ----------------------------------------------
    missing = state.data.missing_for_quote()
    if missing:
        progress = bool(changed) or bool(
            intents & {Intent.DUVIDA, Intent.SAUDACAO, Intent.CONFIRMA}
        )
        state.turns_without_progress = 0 if progress else state.turns_without_progress + 1
        if state.turns_without_progress >= MAX_TURNS_WITHOUT_PROGRESS:
            return Decision(
                "R17_sem_progresso",
                handoff=HandoffReason.SEM_PROGRESSO,
                handoff_detail=f"{state.turns_without_progress} turnos sem dados novos.",
            )
        ask = missing[:1] if missing[0] == "veiculo_ano" else missing
        extra = " O CEP que ele mandou não tem 8 dígitos: peça de novo." if nlu.cep_invalido else ""
        if state.data_inicio_expirou and "data_inicio" in missing:
            extra += " A data de início que ele tinha passado já ficou para trás: peça uma nova."
        state.data_inicio_expirou = False
        state.stage = Stage.COLETANDO
        return Decision(
            "R18_coletar_dados",
            Directive(
                "pedir_dados",
                f"Peça {_ask(ask)}. Se o lead acabou de informar algo, reconheça "
                f"em poucas palavras.{extra}{answer}",
            ),
        )
    state.turns_without_progress = 0

    # --- Everything known and eligible: quote -------------------------------------------
    wanted = (
        [nlu.plano_id]
        if nlu.plano_id
        else ([state.data.plano_id] if state.data.plano_id else list(PLAN_IDS))
    )
    if quotes and Intent.OUTRO_PLANO in intents and not nlu.plano_id:
        wanted = [p for p in PLAN_IDS if p not in quotes] or list(quotes)
    # A plan already being retried in the background is not quoted again.
    pending = {q.plano_id for q in state.pending_quotes()}
    to_quote = [p for p in wanted if p not in quotes and p not in pending]
    if to_quote:
        directive = Directive("antes_da_cotacao", answer.strip()) if answer else None
        return Decision(
            "R19_cotar",
            directive,
            quote_plans=to_quote,
            present_plans=[p for p in wanted if p in quotes],
        )
    if pending and not intents & {Intent.REENVIAR_COTACAO, Intent.OUTRO_PLANO}:
        return Decision(
            "R16_cotacao_pendente",
            Directive(
                "pendente",
                "Diga que a cotação ainda está sendo calculada e que ela chega aqui "
                f"assim que o sistema responder.{answer}",
            ),
        )

    if intents & {Intent.REENVIAR_COTACAO, Intent.OUTRO_PLANO}:
        return Decision("R20_reapresentar", present_plans=wanted)
    if Intent.DUVIDA in intents:
        return Decision(
            "R21_responder",
            Directive(
                "responder",
                f"Responda à dúvida do lead ({nlu.pergunta}) usando só os FATOS, "
                "e pergunte se quer seguir com algum plano.",
            ),
        )
    return Decision(
        "R22_conversa",
        Directive(
            "conversa",
            "Responda de forma breve ao que o lead disse e pergunte se ele quer seguir "
            "com algum dos planos cotados ou tirar alguma dúvida.",
        ),
    )
