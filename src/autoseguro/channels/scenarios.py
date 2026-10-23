"""Scripted conversations (scenarios/*.toml) run end to end against the real API and LLM.

Each run writes to runs/<name>/:
- transcript.md: the conversation as the lead saw it (masked), plus the outcome;
- trace.txt: every message, decision, quote attempt and handoff, with ids;
- events.jsonl: the structured log lines of the run.

A scenario can change the API's behavior between turns (e.g. a full outage, then recovery)
by restarting the local quote-service with new settings.
"""

from __future__ import annotations

import time
import tomllib
from pathlib import Path
from typing import Any

from autoseguro.local_service import QuoteServiceProcess
from autoseguro.obs import configure_logging
from autoseguro.runtime import build_agent
from autoseguro.trace import render_trace, render_transcript

SERVICE_KEYS = ("failure_rate", "slow_rate", "slow_seconds", "seed")


def _service_settings(api: dict[str, Any]) -> dict[str, Any]:
    return {k: api[k] for k in SERVICE_KEYS if k in api}


def run_scenario(path: Path, out_root: Path = Path("runs")) -> dict[str, Any]:
    spec = tomllib.loads(path.read_text(encoding="utf-8"))
    name = spec["name"]
    run_dir = out_root / name
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = Path("var/runs") / f"{name}.db"
    db_path.unlink(missing_ok=True)
    log_path = run_dir / "events.jsonl"
    log_path.unlink(missing_ok=True)
    configure_logging(log_path)

    api = spec.get("api", {})
    service = QuoteServiceProcess()
    url = service.start(**_service_settings(api))
    agent = build_agent(url, db_path=db_path, deferred_schedule=api.get("deferred_schedule"))
    cid = agent.start(lead_name=spec.get("lead_name"), channel="scenario")
    started = time.monotonic()
    try:
        for turn in spec["turns"]:
            if "api" in turn:
                service.restart(**_service_settings({**api, **turn["api"]}))
                agent.store.event(cid, "scenario_api_changed", **turn["api"])
            if turn.get("wait_deferred"):
                agent.deferred.wait(timeout=300)
            if turn.get("messages"):
                agent.handle(cid, turn["messages"])
        agent.deferred.wait(timeout=300)
    finally:
        agent.deferred.shutdown()
        service.stop()

    state = agent.store.load_state(cid)
    summary = {
        "cenario": name,
        "conversation_id": cid,
        "estagio_final": state.stage.value,
        "desfecho": state.outcome,
        "handoff": state.handoff.reason.value if state.handoff else None,
        "cotacoes": {q.quote_id: f"{q.plano_id}: {q.status}" for q in state.quotes.values()},
        "duracao_s": round(time.monotonic() - started, 1),
    }
    quotes = ", ".join(f"`{k}` {v}" for k, v in summary["cotacoes"].items())
    header = [
        f"# Cenário `{name}`",
        "",
        spec.get("description", "").strip(),
        "",
        f"- Conversa: `{cid}`",
        f"- API: {', '.join(f'{k}={v}' for k, v in api.items()) or 'padrão'}",
        f"- Estágio final: `{summary['estagio_final']}`; desfecho: `{summary['desfecho']}`",
        f"- Handoff: `{summary['handoff']}`" if summary["handoff"] else "- Handoff: nenhum",
        f"- Cotações: {quotes or 'nenhuma'}",
        "",
        "Mensagens do lead aparecem como foram gravadas: com os dados pessoais já mascarados.",
        "O rastro completo (decisões, tentativas na API, handoff) está em `trace.txt`.",
        "",
        "---",
        "",
    ]
    (run_dir / "transcript.md").write_text(
        "\n".join(header) + render_transcript(agent.store, cid) + "\n", encoding="utf-8"
    )
    (run_dir / "trace.txt").write_text(render_trace(agent.store, cid) + "\n", encoding="utf-8")
    return summary
