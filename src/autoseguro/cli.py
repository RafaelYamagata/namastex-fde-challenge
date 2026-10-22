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
