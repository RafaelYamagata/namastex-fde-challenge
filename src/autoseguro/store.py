"""Event store (SQLite): the trace of every conversation.

Append-only tables for messages, quotes, quote attempts, handoffs and generic events
(decisions, stage changes, guardrail blocks), plus the latest state snapshot of each
conversation. Everything stored is masked: message bodies go through the redactor before
they get here, and quote requests keep only the CEP prefix. The one exception is the state
snapshot, which holds the full CEP because the agent needs it to quote again.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from autoseguro.agent.state import ConversationState, HandoffRecord
from autoseguro.obs import new_id, utcnow
from autoseguro.quote.models import QuoteResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    stage TEXT NOT NULL,
    outcome TEXT,
    state_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    direction TEXT NOT NULL,          -- in | out
    kind TEXT NOT NULL,               -- text | audio | imagem | documento
    body_masked TEXT NOT NULL,
    status TEXT NOT NULL,             -- received | sent | blocked_rewritten
    meta_json TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotes (
    quote_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    plano_id TEXT NOT NULL,
    request_json TEXT NOT NULL,       -- CEP reduced to its prefix
    status TEXT NOT NULL,             -- ok | refused | invalid | unavailable
    premio_mensal REAL,
    detail TEXT,
    mode TEXT NOT NULL,               -- inline | deferred
    attempts INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quote_attempts (
    quote_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    round INTEGER NOT NULL,
    attempt INTEGER NOT NULL,
    status_code INTEGER,
    error TEXT,
    latency_ms REAL NOT NULL,
    started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS handoffs (
    handoff_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    priority TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def _exec(self, sql: str, params: tuple[Any, ...]) -> None:
        with self._lock:
            self._db.execute(sql, params)
            self._db.commit()

    def _query(self, sql: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    # -- state ------------------------------------------------------------------------
    def create_conversation(self, state: ConversationState, channel: str) -> None:
        now = utcnow().isoformat()
        self._exec(
            "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?)",
            (state.conversation_id, channel, now, now, state.stage, state.outcome, state.to_json()),
        )

    def save_state(self, state: ConversationState) -> None:
        self._exec(
            "UPDATE conversations SET updated_at = ?, stage = ?, outcome = ?, state_json = ? "
            "WHERE conversation_id = ?",
            (
                utcnow().isoformat(),
                state.stage,
                state.outcome,
                state.to_json(),
                state.conversation_id,
            ),
        )

    def load_state(self, conversation_id: str) -> ConversationState:
        rows = self._query(
            "SELECT state_json FROM conversations WHERE conversation_id = ?", (conversation_id,)
        )
        if not rows:
            raise KeyError(conversation_id)
        return ConversationState.from_json(rows[0]["state_json"])

    # -- append-only records ------------------------------------------------------------
    def add_message(
        self,
        conversation_id: str,
        direction: str,
        kind: str,
        body_masked: str,
        status: str,
        **meta: Any,
    ) -> str:
        message_id = new_id("msg")
        self._exec(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message_id,
                conversation_id,
                direction,
                kind,
                body_masked,
                status,
                json.dumps(meta, ensure_ascii=False, default=str),
                utcnow().isoformat(),
            ),
        )
        return message_id

    def record_quote(self, conversation_id: str, result: QuoteResult, mode: str) -> None:
        payload = result.request.payload()
        payload["cep"] = payload["cep"][:5] + "-***"
        premio = result.data["premio_mensal"] if result.data else None
        self._exec(
            "INSERT INTO quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.quote_id,
                conversation_id,
                result.request.plano_id,
                json.dumps(payload),
                result.status.value,
                premio,
                result.detail,
                mode,
                len(result.attempts),
                utcnow().isoformat(),
            ),
        )

    def record_attempts(self, conversation_id: str, result: QuoteResult, round_no: int) -> None:
        for a in result.attempts:
            self._exec(
                "INSERT INTO quote_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result.quote_id,
                    conversation_id,
                    round_no,
                    a.number,
                    a.status_code,
                    a.error,
                    round(a.latency_ms, 1),
                    a.started_at.isoformat(),
                ),
            )

    def add_handoff(self, conversation_id: str, record: HandoffRecord, summary: dict) -> None:
        self._exec(
            "INSERT INTO handoffs VALUES (?, ?, ?, ?, ?, ?)",
            (
                record.handoff_id,
                conversation_id,
                record.reason.value,
                record.priority,
                json.dumps(summary, ensure_ascii=False, default=str),
                record.created_at,
            ),
        )

    def event(self, conversation_id: str, type_: str, **payload: Any) -> None:
        self._exec(
            "INSERT INTO events (conversation_id, type, payload_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                conversation_id,
                type_,
                json.dumps(payload, ensure_ascii=False, default=str),
                utcnow().isoformat(),
            ),
        )

    # -- reading the trace --------------------------------------------------------------
    def timeline(self, conversation_id: str) -> list[dict[str, Any]]:
        """Every record of one conversation, in time order."""
        p = (conversation_id,)
        items: list[dict[str, Any]] = []
        for r in self._query("SELECT * FROM messages WHERE conversation_id = ?", p):
            items.append({"at": r["created_at"], "type": f"message_{r['direction']}", **dict(r)})
        for r in self._query("SELECT * FROM quotes WHERE conversation_id = ?", p):
            items.append({"at": r["created_at"], "type": "quote", **dict(r)})
        for r in self._query("SELECT * FROM quote_attempts WHERE conversation_id = ?", p):
            items.append({"at": r["started_at"], "type": "quote_attempt", **dict(r)})
        for r in self._query("SELECT * FROM handoffs WHERE conversation_id = ?", p):
            items.append({"at": r["created_at"], "type": "handoff", **dict(r)})
        for r in self._query("SELECT * FROM events WHERE conversation_id = ?", p):
            items.append(
                {
                    "at": r["created_at"],
                    "type": r["type"],
                    "seq": r["seq"],
                    **json.loads(r["payload_json"]),
                }
            )
        return sorted(items, key=lambda i: i["at"])

    def conversations(self) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT conversation_id, channel, created_at, stage, outcome FROM conversations "
            "ORDER BY created_at DESC",
            (),
        )
        return [dict(r) for r in rows]
