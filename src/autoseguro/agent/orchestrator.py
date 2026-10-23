"""The agent: one turn = ingest -> understand -> decide -> act -> check -> send.

- ingest: every inbound message gets an id, is masked, and its CEP captured by regex;
- understand: the LLM extracts data and intents (validated by code, see nlu.py);
- decide: the rules engine picks the action (policy.py);
- act: quote (inline, then in the background if the API won't answer), present quotes
  from templates, write the reply with the LLM, or hand off to a human;
- check and send: every outbound message passes the guardrails and is stored.

The agent never leaves a lead without an answer: if the LLM fails while understanding, the
conversation goes to a human; if it fails while writing, a fixed message is sent instead.
"""

from __future__ import annotations

import datetime as dt
import re
import threading
import traceback
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from autoseguro.agent import messages as msg
from autoseguro.agent.guardrails import allowed_amounts, check_outbound
from autoseguro.agent.llm import LLM, LLMError
from autoseguro.agent.nlu import NluResult, understand
from autoseguro.agent.policy import Decision, Directive, Turn, apply_nlu, decide
from autoseguro.agent.state import (
    HANDOFF_PRIORITY,
    ConversationState,
    HandoffReason,
    HandoffRecord,
    QuoteRecord,
    Stage,
)
from autoseguro.clock import today as business_today
from autoseguro.obs import log_event, new_id, utcnow
from autoseguro.privacy.redact import PiiKind, redact
from autoseguro.quote import (
    PLAN_IDS,
    DeferredQuotes,
    PlanCatalog,
    QuoteClient,
    QuoteRequest,
    QuoteResult,
    QuoteStatus,
)
from autoseguro.store import Store

MEDIA_RE = re.compile(r"^\s*\[(audio|áudio|imagem|documento)\]", re.IGNORECASE)

NLG_SYSTEM = """\
Você é a assistente virtual da AutoSeguro no WhatsApp e ajuda leads a cotar seguro auto.
Escreva SOMENTE a próxima mensagem para o lead, em português do Brasil, com tom cordial e \
direto de WhatsApp: no máximo 3 frases curtas, sem títulos nem listas longas, no máximo \
um emoji.

Regras que você nunca quebra:
1. Não escreva preços, mensalidades nem valores em R$: quem envia a cotação é o sistema, \
automaticamente, quando ela fica pronta. Não comente esse mecanismo com o lead. A única \
exceção são os valores de franquia listados nos FATOS, quando o lead perguntar.
2. Não peça CPF, e-mail, placa, documentos nem fotos: a cotação não precisa deles.
3. Não prometa desconto, condição especial, aprovação ou prazo.
4. Para falar de planos, coberturas, franquia, carência ou pagamento, use só os FATOS. Se \
não souber, diga que um especialista pode confirmar.
5. Siga a INSTRUÇÃO DESTA RESPOSTA. Só fale em passar para um especialista se a instrução \
pedir.
6. [CEP], [CPF], [NOME], [TELEFONE], [EMAIL] e [PLACA] são dados removidos por privacidade: \
não os repita nem tente adivinhar o conteúdo.
7. Você é uma assistente virtual, não uma pessoa; não finja o contrário.

FATOS:
"""

STRICT_RETRY = (
    "\n\nATENÇÃO: sua resposta anterior trazia valores em R$ que não podem ser enviados. "
    "Reescreva sem nenhum valor numérico de dinheiro."
)


class Agent:
    def __init__(
        self,
        *,
        llm: LLM,
        quote_client: QuoteClient,
        catalog: PlanCatalog,
        store: Store,
        deferred: DeferredQuotes,
        send: Callable[[str, str], None] | None = None,
        today: Callable[[], dt.date] = business_today,
    ) -> None:
        self.llm = llm
        self.quote_client = quote_client
        self.catalog = catalog
        self.store = store
        self.deferred = deferred
        self._send_cb = send or (lambda conversation_id, text: None)
        self._today = today
        self._locks: defaultdict[str, threading.RLock] = defaultdict(threading.RLock)
        self._nlg_system = NLG_SYSTEM + msg.plan_facts(catalog)

    # -- public API -----------------------------------------------------------------------
    def start(self, lead_name: str | None = None, channel: str = "cli") -> str:
        state = ConversationState(conversation_id=new_id("conv"), lead_name=lead_name)
        self.store.create_conversation(state, channel)
        self.store.event(
            state.conversation_id,
            "conversation_started",
            channel=channel,
            catalog_source=self.catalog.source,
        )
        log_event("conversation_started", conversation_id=state.conversation_id, channel=channel)
        return state.conversation_id

    def handle(self, conversation_id: str, raw_messages: list[str]) -> list[str]:
        """Process one turn: a burst of messages from the lead. Returns what was sent."""
        with self._locks[conversation_id]:
            state = self.store.load_state(conversation_id)
            turn, masked = self._ingest(state, raw_messages)
            try:
                out = self._turn(state, turn, masked)
            except LLMError as exc:
                self.store.event(conversation_id, "llm_failed", stage="nlu", error=str(exc))
                out = self._handoff(
                    state,
                    HandoffReason.ERRO_INTERNO,
                    f"LLM indisponível ao interpretar a mensagem ({exc}).",
                    rule="R00_llm_indisponivel",
                )
            except Exception as exc:  # a lead must never be left without an answer
                self.store.event(
                    conversation_id,
                    "internal_error",
                    error=repr(exc),
                    traceback=traceback.format_exc(limit=5),
                )
                out = self._handoff(
                    state,
                    HandoffReason.ERRO_INTERNO,
                    f"Erro interno: {exc!r}",
                    rule="R00_erro_interno",
                )
            self.store.save_state(state)
            return out

    # -- one turn -------------------------------------------------------------------------
    def _ingest(self, state: ConversationState, raw_messages: list[str]) -> tuple[Turn, list[str]]:
        turn, masked = Turn(), []
        names = [state.lead_name] if state.lead_name else []
        for raw in raw_messages:
            media = MEDIA_RE.match(raw)
            kind = media.group(1).lower().replace("á", "a") if media else "text"
            redaction = redact(raw, names=names)
            self.store.add_message(
                state.conversation_id,
                "in",
                kind,
                redaction.text,
                "received",
                pii=sorted({m.kind.value for m in redaction.matches}),
            )
            masked.append(redaction.text)
            if kind == "text":
                turn.texts.append(redaction.text)
                turn.ceps.extend(redaction.values(PiiKind.CEP))
            else:
                turn.media.append(kind)
        return turn, masked

    def _turn(self, state: ConversationState, turn: Turn, masked: list[str]) -> list[str]:
        cid = state.conversation_id
        if state.stage is Stage.HANDOFF:
            state.transcript.extend(("lead", m) for m in masked)
            self.store.event(cid, "message_during_handoff", handoff_id=state.handoff.handoff_id)
            if state.handoff_acknowledged:
                return []
            state.handoff_acknowledged = True
            return [self._say(state, msg.HANDOFF_ACK, source="template")]
        if state.stage is Stage.ENCERRADO:
            state.stage = Stage.COLETANDO
            self.store.event(cid, "conversation_reopened")

        today = self._today()
        nlu = understand(self.llm, state, turn.texts, today) if turn.texts else NluResult()
        changed = apply_nlu(state, nlu, turn)
        state.transcript.extend(("lead", m) for m in masked)
        self.store.event(
            cid,
            "nlu",
            intents=sorted(nlu.intents),
            changed=changed,
            dropped=nlu.dropped,
            media=turn.media,
        )

        stage_before = state.stage
        decision = decide(state, nlu, turn, changed, self.catalog, today)
        self.store.event(
            cid,
            "decision",
            rule=decision.rule,
            directive=decision.directive.code if decision.directive else None,
            quote_plans=decision.quote_plans,
            present_plans=decision.present_plans,
            handoff=decision.handoff,
        )
        out = self._execute(state, decision)
        if state.stage is not stage_before:
            self.store.event(cid, "stage_changed", from_=stage_before, to=state.stage)
        return out

    def _execute(self, state: ConversationState, decision: Decision) -> list[str]:
        if decision.handoff:
            return self._handoff(state, decision.handoff, decision.handoff_detail, decision.rule)
        out = []
        if decision.directive and decision.directive.instruction:
            out.append(self._write(state, decision.directive))
        if decision.quote_plans:
            if not out:
                out.append(self._say(state, msg.PROGRESS, source="template"))
            out += self._quote(state, decision.quote_plans, decision.present_plans)
        elif decision.present_plans:
            current = state.current_quotes()
            records = [current[p] for p in PLAN_IDS if p in decision.present_plans and p in current]
            if records:
                out.append(self._present(state, records))
        return out

    # -- quoting --------------------------------------------------------------------------
    def _quote(
        self, state: ConversationState, plans: list[str], also_present: list[str]
    ) -> list[str]:
        cid, d = state.conversation_id, state.data
        requests = {
            p: QuoteRequest(
                plano_id=p,
                idade=d.idade,
                veiculo_ano=d.veiculo_ano,
                cep=d.cep,
                data_inicio=d.data_inicio,
            )
            for p in plans
        }
        ids = {p: new_id("q") for p in plans}

        def run(plan: str) -> QuoteResult:
            return self.quote_client.quote(requests[plan], quote_id=ids[plan], conversation_id=cid)

        with ThreadPoolExecutor(max_workers=len(plans)) as pool:
            results = dict(zip(plans, pool.map(run, plans), strict=True))

        fingerprint = d.fingerprint()
        for plan, result in results.items():
            self.store.record_quote(cid, result, mode="inline")
            self.store.record_attempts(cid, result, round_no=0)
            state.quotes[result.quote_id] = QuoteRecord(
                result.quote_id, plan, fingerprint, result.status.value, result.data, result.detail
            )

        by_status = defaultdict(list)
        for result in results.values():
            by_status[result.status].append(result)
        if by_status[QuoteStatus.REFUSED]:
            refused = by_status[QuoteStatus.REFUSED][0]
            return self._handoff(
                state,
                HandoffReason.INELEGIVEL,
                f"API recusou a cotação {refused.quote_id}: {refused.detail}",
                rule="R15b_recusa_da_api",
            )
        if by_status[QuoteStatus.INVALID]:
            invalid = by_status[QuoteStatus.INVALID][0]
            return self._handoff(
                state,
                HandoffReason.ERRO_INTERNO,
                f"API rejeitou o payload da cotação {invalid.quote_id}: {invalid.detail}",
                rule="R00_payload_invalido",
            )

        out = []
        current = state.current_quotes()
        shown = [current[p] for p in PLAN_IDS if p in current and (p in plans or p in also_present)]
        if shown:
            out.append(self._present(state, shown))
        unavailable = by_status[QuoteStatus.UNAVAILABLE]
        if unavailable:
            for result in unavailable:
                record = state.quotes[result.quote_id]
                record.status, record.deferred = "pending", True
                self.deferred.submit(
                    requests[record.plano_id],
                    quote_id=result.quote_id,
                    conversation_id=cid,
                    on_done=partial(self._on_deferred_done, cid),
                    on_round=partial(self._on_deferred_round, cid),
                )
            names = [self.catalog.plan(r.request.plano_id)["nome"] for r in unavailable]
            self.store.event(cid, "quote_deferred", quote_ids=[r.quote_id for r in unavailable])
            out.append(
                self._say(
                    state, msg.deferred_notice(names, some_presented=bool(shown)), source="template"
                )
            )
            if not shown:
                state.stage = Stage.AGUARDANDO_COTACAO
        return out

    def _present(
        self, state: ConversationState, records: list[QuoteRecord], intro: str | None = None
    ) -> str:
        state.stage = Stage.COTADO
        state.outcome = state.outcome or "cotado"
        text = msg.present_quotes(records, state.data, intro=intro)
        return self._say(state, text, source="template", quote_ids=[r.quote_id for r in records])

    def _on_deferred_round(self, cid: str, result: QuoteResult, round_no: int) -> None:
        self.store.record_attempts(cid, result, round_no=round_no)

    def _on_deferred_done(self, cid: str, result: QuoteResult) -> None:
        with self._locks[cid]:
            state = self.store.load_state(cid)
            stage_before = state.stage
            self.store.record_quote(cid, result, mode="deferred")
            record = state.quotes[result.quote_id]
            record.status, record.data, record.detail = (
                result.status.value,
                result.data,
                result.detail,
            )
            stale = record.fingerprint != state.data.fingerprint()
            if state.stage is Stage.HANDOFF or stale:
                self.store.event(
                    cid,
                    "deferred_result_not_sent",
                    quote_id=result.quote_id,
                    status=result.status,
                    reason="handoff" if not stale else "stale",
                )
            elif result.status is QuoteStatus.OK:
                self._present(
                    state, [record], intro="Saiu! Aqui está a cotação que estava pendente:"
                )
            elif result.status is QuoteStatus.REFUSED:
                self._handoff(
                    state,
                    HandoffReason.INELEGIVEL,
                    f"API recusou a cotação {result.quote_id}: {result.detail}",
                    rule="R15b_recusa_da_api",
                )
            elif result.status is QuoteStatus.INVALID:
                self._handoff(
                    state,
                    HandoffReason.ERRO_INTERNO,
                    f"API rejeitou o payload da cotação {result.quote_id}",
                    rule="R00_payload_invalido",
                )
            elif state.current_quotes():
                plan = self.catalog.plan(record.plano_id)["nome"]
                self._say(
                    state,
                    f"Não consegui calcular o plano {plan} agora. Se ele te "
                    "interessar, me avisa que eu chamo um especialista pra te passar o "
                    "valor.",
                    source="template",
                )
            else:
                self._handoff(
                    state,
                    HandoffReason.COTACAO_INDISPONIVEL,
                    f"Cotação {result.quote_id} sem resposta da API após as "
                    "tentativas em segundo plano.",
                    rule="R23_cotacao_indisponivel",
                )
            if state.stage is not stage_before:
                self.store.event(cid, "stage_changed", from_=stage_before, to=state.stage)
            self.store.save_state(state)

    # -- handoff --------------------------------------------------------------------------
    def _handoff(
        self, state: ConversationState, reason: HandoffReason, detail: str, rule: str
    ) -> list[str]:
        if state.stage is Stage.HANDOFF:
            return []
        d = state.data
        record = HandoffRecord(
            new_id("ho"), reason, HANDOFF_PRIORITY[reason], detail, utcnow().isoformat()
        )
        summary = {
            "motivo": reason.value,
            "prioridade": record.priority,
            "regra": rule,
            "detalhe": detail,
            "dados": {
                "idade": d.idade,
                "veiculo": f"{d.veiculo_modelo or ''} {d.veiculo_ano or ''}".strip() or None,
                "cep": f"{d.cep[:5]}-***" if d.cep else None,
                "plano_de_interesse": d.plano_id,
                "data_inicio": d.data_inicio,
            },
            "cotacoes": [
                {
                    "quote_id": q.quote_id,
                    "plano": q.plano_id,
                    "status": q.status,
                    "premio_mensal": q.data["premio_mensal"] if q.data else None,
                }
                for q in state.quotes.values()
            ],
            "ultimas_mensagens": state.transcript[-6:],
        }
        self.store.add_handoff(state.conversation_id, record, summary)
        log_event(
            "handoff",
            conversation_id=state.conversation_id,
            handoff_id=record.handoff_id,
            reason=reason,
            priority=record.priority,
        )
        text = self._say(state, msg.HANDOFF_MESSAGES[reason], source="template")
        state.handoff, state.stage = record, Stage.HANDOFF
        state.outcome = state.outcome if state.outcome == "fechamento" else reason.value
        return [text]

    # -- writing and sending ----------------------------------------------------------------
    def _write(self, state: ConversationState, directive: Directive) -> str:
        allowed = allowed_amounts(state, self.catalog)
        prompt = self._nlg_prompt(state, directive)
        for attempt in (1, 2):
            try:
                text = self.llm.complete(
                    system=self._nlg_system,
                    user=prompt if attempt == 1 else prompt + STRICT_RETRY,
                    purpose="nlg",
                    conversation_id=state.conversation_id,
                )
            except LLMError as exc:
                self.store.event(state.conversation_id, "llm_failed", stage="nlg", error=str(exc))
                break
            check = check_outbound(text, allowed)
            if not check.unbacked:
                return self._say(state, text, source="llm", directive=directive.code)
            self.store.event(
                state.conversation_id,
                "guardrail_blocked",
                directive=directive.code,
                unbacked=check.unbacked,
                attempt=attempt,
            )
        missing = state.data.missing_for_quote()
        fallback = (
            msg.ask_fields_fallback(missing)
            if directive.code == "pedir_dados" and missing
            else msg.GENERIC_FALLBACK
        )
        return self._say(state, fallback, source="fallback", directive=directive.code)

    def _nlg_prompt(self, state: ConversationState, directive: Directive) -> str:
        d = state.data
        known = [
            f"idade {d.idade}" if d.idade is not None else None,
            f"carro {d.veiculo_modelo or ''} {d.veiculo_ano or ''}".strip()
            if d.veiculo_ano or d.veiculo_modelo
            else None,
            "CEP informado" if d.cep else None,
            f"início {d.data_inicio.strftime('%d/%m/%Y')}" if d.data_inicio else None,
            f"plano de interesse {d.plano_id}" if d.plano_id else None,
        ]
        quoted = [self.catalog.plan(p)["nome"] for p in state.current_quotes()]
        recent = "\n".join(f"{role}: {text}" for role, text in state.transcript[-10:])
        first = (
            "Esta é a primeira mensagem do agente na conversa: cumprimente e apresente-se como "
            "assistente virtual da AutoSeguro.\n"
            if not state.greeted
            else ""
        )
        return (
            f"INSTRUÇÃO DESTA RESPOSTA: {directive.instruction}\n{first}"
            f"Dados já informados pelo lead: {', '.join(k for k in known if k) or 'nenhum'}\n"
            f"Planos já cotados e enviados: {', '.join(quoted) or 'nenhum'}\n\n"
            f"Conversa recente:\n{recent}"
        )

    def _say(self, state: ConversationState, text: str, *, source: str, **meta: object) -> str:
        """The single exit for outbound messages: guardrails, storage, delivery."""
        check = check_outbound(text, allowed_amounts(state, self.catalog))
        status = "sent"
        if check.unbacked:  # a template should never get here; block it anyway
            self.store.event(
                state.conversation_id, "guardrail_blocked", source=source, unbacked=check.unbacked
            )
            text, status = msg.GENERIC_FALLBACK, "blocked_rewritten"
        else:
            text = check.text
        if check.pii_masked:
            self.store.event(state.conversation_id, "outbound_pii_masked", kinds=check.pii_masked)
        message_id = self.store.add_message(
            state.conversation_id, "out", "text", text, status, source=source, **meta
        )
        log_event(
            "message_sent",
            conversation_id=state.conversation_id,
            message_id=message_id,
            source=source,
        )
        state.transcript.append(("agente", text))
        state.greeted = True
        self._send_cb(state.conversation_id, text)
        return text
