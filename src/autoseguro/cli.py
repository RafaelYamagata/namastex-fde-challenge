"""Command line entry point: `uv run autoseguro --help`."""

from __future__ import annotations

from pathlib import Path

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


if __name__ == "__main__":
    app()
