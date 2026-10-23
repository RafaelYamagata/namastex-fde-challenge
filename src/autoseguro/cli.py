"""Command line entry point: `uv run autoseguro --help`."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import typer
from dotenv import load_dotenv

load_dotenv()

app = typer.Typer(no_args_is_help=True, help="Agente de cotação AutoSeguro.")


@app.callback()
def main() -> None:
    """Agente de cotação AutoSeguro."""


@app.command()
def bench(
    rates: str = typer.Option("0.2,0.5,1.0", help="Taxas de falha da API, separadas por vírgula"),
    n: int = typer.Option(200, help="Cotações por taxa e por cliente"),
    concurrency: int = typer.Option(8),
    seed: int = typer.Option(42, help="QUOTE_SEED do serviço"),
    out: Path | None = typer.Option(None, help="Salva a tabela em Markdown neste arquivo"),
) -> None:
    """Mede o cliente resiliente contra um cliente ingênuo, com a API real."""
    from autoseguro.quote.bench import run_bench, to_markdown

    table = to_markdown(
        run_bench([float(r) for r in rates.split(",")], n=n, concurrency=concurrency, seed=seed)
    )
    typer.echo(table)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(table + "\n", encoding="utf-8")


def _api_url(api_url: str | None) -> str:
    return api_url or os.environ.get("QUOTE_API_URL", "http://localhost:8000")


@app.command()
def chat(
    lead_name: str = typer.Option("Lead Teste", help="Nome do perfil do WhatsApp (simulado)"),
    api_url: str | None = typer.Option(None, help="URL da API de cotação (padrão: QUOTE_API_URL)"),
    spawn_api: bool = typer.Option(
        False, help="Sobe uma instância local da API (taxa de falha padrão) em vez de usar a URL"
    ),
) -> None:
    """Conversa com o agente pelo terminal."""
    from autoseguro.channels.cli_chat import run_chat
    from autoseguro.local_service import run_quote_service
    from autoseguro.obs import configure_logging

    configure_logging(Path("var/logs/agent.jsonl"))
    if spawn_api:
        with run_quote_service() as url:
            run_chat(url, lead_name)
        return
    url = _api_url(api_url)
    try:
        httpx.get(f"{url}/health", timeout=2).raise_for_status()
    except httpx.HTTPError:
        typer.secho(
            f"API de cotação fora do ar em {url}. Rode `docker compose up` ou use --spawn-api.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1) from None
    run_chat(url, lead_name)


@app.command()
def trace(
    conversation_id: str,
    db: Path = typer.Option(Path("var/agent.db"), help="Banco de eventos"),
) -> None:
    """Mostra o rastro completo de uma conversa: mensagens, decisões, cotações, handoff."""
    from autoseguro.store import Store
    from autoseguro.trace import render_trace

    typer.echo(render_trace(Store(db), conversation_id))


@app.command()
def conversations(db: Path = typer.Option(Path("var/agent.db"))) -> None:
    """Lista as conversas gravadas."""
    from autoseguro.store import Store

    for c in Store(db).conversations():
        typer.echo(
            f"{c['created_at'][:19]}  {c['conversation_id']}  {c['stage']:<20} {c['outcome']}"
        )


@app.command("run-scenario")
def run_scenario_cmd(
    paths: list[Path] = typer.Argument(..., help="Arquivos de cenário (scenarios/*.toml)"),
) -> None:
    """Roda conversas roteirizadas de ponta a ponta e grava os logs em runs/<nome>/."""
    from autoseguro.channels.scenarios import run_scenario

    for path in paths:
        typer.secho(f"▶ {path}", bold=True)
        for key, value in run_scenario(path).items():
            typer.echo(f"  {key}: {value}")


data_app = typer.Typer(no_args_is_help=True, help="Pipeline do dataset (Bronze → Silver → Gold).")
app.add_typer(data_app, name="data")


@data_app.command("silver")
def data_silver() -> None:
    """Ordena, mascara e estrutura o dataset (var/data/silver)."""
    from autoseguro.data import silver

    for key, value in silver.run().items():
        typer.echo(f"{key}: {value}")


@data_app.command("audit")
def data_audit(
    api_url: str | None = typer.Option(
        None, help="URL da API de cotação; sem ela, sobe uma instância local com a taxa padrão"
    ),
) -> None:
    """Recota o histórico na API oficial e gera docs/achados-dataset.md."""
    from autoseguro.data import audit
    from autoseguro.local_service import run_quote_service

    if api_url:
        result = audit.run(api_url)
    else:
        with run_quote_service() as url:
            result = audit.run(url)
    typer.echo(result.status_api.value_counts().to_string())
    typer.echo("relatório: docs/achados-dataset.md")


if __name__ == "__main__":
    app()
