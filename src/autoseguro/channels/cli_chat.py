"""Interactive chat in the terminal, standing in for WhatsApp.

- each line is one message; `||` splits a line into a burst of messages (one turn);
- a line starting with `[audio]`, `[imagem]` or `[documento]` simulates media;
- `/trace` prints the trace so far, `/sair` quits.
Background quotes arrive on their own, like a proactive WhatsApp message.
"""

from __future__ import annotations

from pathlib import Path

import typer

from autoseguro.runtime import DEFAULT_DB, build_agent
from autoseguro.trace import render_trace


def _print_agent(conversation_id: str, text: str) -> None:
    typer.secho("\n🤖 " + text.replace("\n", "\n   "), fg=typer.colors.CYAN)


def run_chat(api_url: str, lead_name: str | None, db_path: Path = DEFAULT_DB) -> None:
    agent = build_agent(api_url, db_path=db_path, send=_print_agent)
    cid = agent.start(lead_name=lead_name, channel="cli")
    typer.secho(f"Conversa {cid} (API {api_url}, planos: {agent.catalog.source})", bold=True)
    typer.echo(
        "Dicas: '||' separa mensagens em rajada; '[audio] ...' simula mídia; "
        "/trace mostra o rastro; /sair encerra.\n"
    )
    while True:
        try:
            line = input("você> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line == "/sair":
            break
        if line == "/trace":
            typer.echo(render_trace(agent.store, cid))
            continue
        agent.handle(cid, [part.strip() for part in line.split("||") if part.strip()])
    agent.deferred.shutdown()
    typer.echo(f"\nRastro completo: uv run autoseguro trace {cid}")
