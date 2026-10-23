"""Wiring: builds an Agent from settings (env vars, see .env.example)."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path

from autoseguro.agent.llm import LLM, ClaudeLLM
from autoseguro.agent.orchestrator import Agent
from autoseguro.quote import DeferredQuotes, PlanCatalog, QuoteClient
from autoseguro.quote.deferred import DEFAULT_SCHEDULE_S
from autoseguro.store import Store

DEFAULT_DB = Path("var/agent.db")


def deferred_schedule_from_env() -> tuple[float, ...]:
    raw = os.environ.get("DEFERRED_SCHEDULE_S")
    return tuple(float(x) for x in raw.split(",")) if raw else DEFAULT_SCHEDULE_S


def build_agent(
    api_url: str,
    *,
    db_path: Path = DEFAULT_DB,
    send: Callable[[str, str], None] | None = None,
    deferred_schedule: Sequence[float] | None = None,
    llm: LLM | None = None,
) -> Agent:
    client = QuoteClient(api_url)
    return Agent(
        llm=llm or ClaudeLLM(),
        quote_client=client,
        catalog=PlanCatalog.load(api_url),
        store=Store(db_path),
        deferred=DeferredQuotes(client, deferred_schedule or deferred_schedule_from_env()),
        send=send,
    )
