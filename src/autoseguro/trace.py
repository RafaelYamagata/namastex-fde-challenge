"""Human-readable rendering of one conversation's trace (`autoseguro trace <id>`)."""

from __future__ import annotations

import json
from typing import Any

from autoseguro.br_formats import format_brl
from autoseguro.store import Store

SKIP_FIELDS = {"at", "type", "seq", "conversation_id"}


def _time(iso: str) -> str:
    return iso[11:23]


def _indent(text: str) -> str:
    return text.replace("\n", "\n" + " " * 16)


def render_line(item: dict[str, Any]) -> str:
    t, kind = _time(item["at"]), item["type"]
    if kind == "message_in":
        return (
            f"{t}  ← lead     [{item['kind']}] {item['message_id']}: {_indent(item['body_masked'])}"
        )
    if kind == "message_out":
        meta = json.loads(item["meta_json"] or "{}")
        source = meta.get("source", "?")
        status = "" if item["status"] == "sent" else f" ({item['status']})"
        return (
            f"{t}  → agente   [{source}{status}] {item['message_id']}:\n"
            f"{' ' * 16}{_indent(item['body_masked'])}"
        )
    if kind == "quote_attempt":
        outcome = item["error"] or "ok"
        code = item["status_code"] if item["status_code"] is not None else "---"
        return (
            f"{t}  $ tentativa {item['quote_id']} rodada {item['round']} #{item['attempt']}: "
            f"HTTP {code} {outcome} ({item['latency_ms']:.0f} ms)"
        )
    if kind == "quote":
        price = f" {format_brl(item['premio_mensal'])}/mês" if item["premio_mensal"] else ""
        detail = f" ({item['detail']})" if item["detail"] and not price else ""
        return (
            f"{t}  $ cotação  {item['quote_id']} {item['plano_id']}: {item['status']}{price}"
            f"{detail} [{item['mode']}, {item['attempts']} tentativa(s)]"
        )
    if kind == "handoff":
        summary = json.loads(item["summary_json"])
        return (
            f"{t}  ! HANDOFF  {item['handoff_id']} motivo={item['reason']} "
            f"prioridade={item['priority']}: {summary['detalhe']}"
        )
    fields = {k: v for k, v in item.items() if k not in SKIP_FIELDS and v not in (None, [], "")}
    return f"{t}  · {kind:<9} {json.dumps(fields, ensure_ascii=False, default=str)}"


def render_trace(store: Store, conversation_id: str) -> str:
    lines = [f"Trace da conversa {conversation_id} (horários em UTC)", ""]
    lines += [render_line(item) for item in store.timeline(conversation_id)]
    return "\n".join(lines)


def render_transcript(store: Store, conversation_id: str) -> str:
    """Just the conversation, as the lead saw it (masked)."""
    lines = []
    for item in store.timeline(conversation_id):
        if item["type"] == "message_in":
            lines.append(f"**Lead** ({_time(item['at'])}): {item['body_masked']}")
        elif item["type"] == "message_out":
            body = item["body_masked"].replace("\n", "  \n")
            lines.append(f"**Agente** ({_time(item['at'])}):  \n{body}")
        elif item["type"] == "handoff":
            lines.append(
                f"> *Handoff `{item['handoff_id']}`: {item['reason']} "
                f"(prioridade {item['priority']})*"
            )
    return "\n\n".join(lines)
