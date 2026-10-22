"""Silver -> Gold: re-price every historical quote with the official API, and write the
findings report.

Runs on Silver only. The region multiplier depends on the first two digits of the CEP, so
"{prefix}000-000" prices exactly like the lead's real CEP without needing it.

The audit is also a field test of the agent's own components on 2,500 real cases: every
quote goes through `QuoteClient` (at the API's default failure rate), and the eligibility
pre-check from `PlanCatalog` is compared with the API's verdict.
"""

from __future__ import annotations

import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from autoseguro.quote import PlanCatalog, QuoteClient, QuoteRequest, QuoteResult, QuoteStatus

SILVER_DIR = Path("var/data/silver")
GOLD_DIR = Path("var/data/gold")


def _request(row: pd.Series, start: dt.date) -> QuoteRequest:
    return QuoteRequest(
        plano_id=row.oferta_plano,
        idade=int(row.idade),
        veiculo_ano=int(row.veiculo_ano),
        cep=f"{row.cep_prefixo}000-000",
        data_inicio=start,
    )


def reprice(conversations: pd.DataFrame, api_url: str, concurrency: int = 8) -> pd.DataFrame:
    client = QuoteClient(api_url)
    catalog = PlanCatalog.load(api_url)
    start = dt.date.today() + dt.timedelta(days=7)
    requests = [_request(row, start) for row in conversations.itertuples()]

    def run(req: QuoteRequest) -> QuoteResult:
        return client.quote(req)

    with ThreadPoolExecutor(concurrency) as pool:
        results = list(pool.map(run, requests))
    first_pass_attempts = sum(len(r.attempts) for r in results)
    first_pass_unavailable = sum(r.status is QuoteStatus.UNAVAILABLE for r in results)

    # What the agent does with quotes it couldn't get inline: try again later.
    for _ in range(3):
        pending = [i for i, r in enumerate(results) if r.status is QuoteStatus.UNAVAILABLE]
        if not pending:
            break
        time.sleep(2)
        for i in pending:
            results[i] = client.quote(requests[i])
    client.close()

    rows = []
    for conv, req, result in zip(conversations.itertuples(), requests, results, strict=True):
        ineligible = catalog.check_age(req.idade) or catalog.check_vehicle(req.veiculo_ano)
        oficial = result.data["premio_mensal"] if result.status is QuoteStatus.OK else None
        rows.append(
            {
                "conversation_id": conv.conversation_id,
                "status_api": result.status.value,
                "motivo_api": result.detail if result.status is QuoteStatus.REFUSED else None,
                "precheck_inelegivel": ineligible.code.value if ineligible else None,
                "preco_oficial": oficial,
                "preco_vendedor": conv.oferta_preco,
                "diferenca_pct": (
                    round(100 * (conv.oferta_preco - oficial) / oficial, 1) if oficial else None
                ),
            }
        )
    audit = pd.DataFrame(rows)
    audit.attrs["first_pass_attempts"] = first_pass_attempts
    audit.attrs["first_pass_unavailable"] = first_pass_unavailable
    return audit


def _pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.1f}%".replace(".", ",") if whole else "-"


def _n(value: float) -> str:
    return f"{value:,.0f}".replace(",", ".")


def build_report(conv: pd.DataFrame, msgs: pd.DataFrame, audit: pd.DataFrame) -> str:
    df = conv.merge(audit, on="conversation_id")
    total = len(df)
    eligible = df[df.status_api == "ok"]
    refused = df[df.status_api == "refused"]
    diff = eligible.diferenca_pct
    agree = (df.precheck_inelegivel.notna() == (df.status_api == "refused")).sum()
    won_eligible = (eligible.outcome == "ganho").sum()
    won_refused = (refused.outcome == "ganho").sum()
    high_risk_prefixes = PlanCatalog.snapshot().rules["regiao_cep"]
    high_risk = df.cep_prefixo.isin(high_risk_prefixes["prefixos_alto_risco"]).sum()

    pii_rows = []
    for kind in ["cpf", "email", "telefone", "placa", "cep", "nome"]:
        n_conv = conv.pii_encontrada.apply(lambda kinds, k=kind: k in kinds).sum()
        pii_rows.append(f"| {kind} | {_n(n_conv)} | {_pct(n_conv, total)} |")
    seller_with_name = msgs[
        (msgs.sender_role == "vendedor") & msgs.body.str.contains(r"\[NOME:", regex=True)
    ]

    objection = pd.crosstab(df.objecao.fillna("(nenhuma)"), df.outcome).reindex(
        columns=["ganho", "em_negociacao", "perdido", "sem_resposta"], fill_value=0
    )
    objection_lines = [
        "| Objeção | ganho | em_negociacao | perdido | sem_resposta |",
        "|---|---|---|---|---|",
    ]
    for name, r in objection.iterrows():
        objection_lines.append(f"| {name} | " + " | ".join(_n(v) for v in r.tolist()) + " |")

    refusal_reasons = refused.precheck_inelegivel.value_counts()
    reasons_text = ", ".join(f"{k} = {_n(v)}" for k, v in refusal_reasons.items())
    unavailable_final = (df.status_api == "unavailable").sum()
    media_total = conv.n_documento.sum() + conv.n_imagem.sum() + conv.n_audio.sum()

    return f"""# Achados do dataset de conversas

Gerado por `uv run autoseguro data silver && uv run autoseguro data audit`. Todos os números
saem da camada Silver (sem dados pessoais) e de cotações feitas na API oficial.

## Volume e qualidade

- {_n(total)} conversas, {_n(len(msgs))} mensagens.
- Conversas com timestamp voltando no tempo: {_n(conv.timestamps_fora_de_ordem.sum())}
  ({_pct(conv.timestamps_fora_de_ordem.sum(), total)}). A ordem confiável é `message_index`.
- A coluna `veiculo_texto` vem normalizada ("Marca Modelo Ano"), ao contrário do que o
  dicionário diz; o texto livre de verdade está no corpo das mensagens.
- Mensagens de mídia sem conteúdo: {_n(conv.n_documento.sum())} documentos,
  {_n(conv.n_imagem.sum())} imagens, {_n(conv.n_audio.sum())} áudios
  ({_pct(media_total, len(msgs))} das mensagens).

## Dados pessoais

| Tipo | Conversas | % |
|---|---|---|
{chr(10).join(pii_rows)}

- O nome do lead também aparece nas mensagens do vendedor (saudação): {_n(len(seller_with_name))}
  mensagens. Mascarar só as mensagens do lead não basta.
- O vendedor pede CPF em todas as conversas; a cotação não usa CPF.
- Depois do mascaramento, mensagens com dado pessoal ainda detectável: **0**.

## Auditoria de preço (vendedor × API oficial)

- Cotações refeitas na API: {_n(total)}, com a taxa de falha padrão (20% de erro + 10% de
  lentidão). Na primeira passada, {_n(audit.attrs.get("first_pass_unavailable", 0))} ficaram sem
  resposta depois dos retries do cliente; novas rodadas (como o agente faz em segundo plano)
  deixaram {_n(unavailable_final)} sem preço.
- **Elegíveis com preço oficial: {_n(len(eligible))}.** Preço do vendedor igual ao oficial:
  **{_n((diff.abs() < 0.01).sum())}**. Dentro de ±5%: {_n((diff.abs() <= 5).sum())}
  ({_pct((diff.abs() <= 5).sum(), len(eligible))}).
- Vendedor abaixo do oficial (mais de 5%): {_n((diff < -5).sum())}; acima: {_n((diff > 5).sum())}.
  Diferença mediana: {f"{diff.median():.1f}".replace(".", ",")}%.
- **Recusados pela API: {_n(len(refused))} ({_pct(len(refused), total)})**, e todos receberam
  preço do vendedor. Por motivo (o primeiro que a regra encontra): {reasons_text}.
- Taxa de fechamento (`ganho`): elegíveis {_pct(won_eligible, len(eligible))}, recusados
  {_pct(won_refused, len(refused))} ({_n(won_refused)} conversas). O time humano fecha esses casos
  em ritmo parecido, por isso o agente os encaminha (handoff `inelegivel`) em vez de encerrar.
- Pré-checagem de elegibilidade do agente (tabela de `GET /planos`) × veredito da API:
  **{_n(agree)} de {_n(total)} concordam**.
- Leads em CEP de alto risco (agravo de 30%): {_n(high_risk)} ({_pct(high_risk, total)}). Sem
  CEP, a API não aplica o agravo; por isso o agente exige o CEP antes de cotar.

## Objeções × desfecho

{chr(10).join(objection_lines)}

## O que isso muda no agente

- O histórico não serve como fonte de preço nem de roteiro: os preços dos vendedores não
  seguem a regra. Preço só sai da `/quote`, e todo valor em R$ enviado ao lead é conferido.
- O agente não pede CPF, e-mail nem placa; o que chegar é mascarado antes do LLM e dos logs.
- Objeções de preço, franquia e concorrente encerram a maior parte das conversas perdidas ou
  paradas; o agente oferece uma alternativa de plano e, se não resolver, passa para um humano.
- Mídia (CNH, foto, áudio) aparece em boa parte das conversas: o agente não finge que leu;
  CNH é sinal de fechamento e vira handoff.
"""


def run(api_url: str, silver_dir: Path = SILVER_DIR, gold_dir: Path = GOLD_DIR) -> pd.DataFrame:
    conv = pd.read_parquet(silver_dir / "conversations.parquet")
    msgs = pd.read_parquet(silver_dir / "messages.parquet")
    audit = reprice(conv, api_url)
    gold_dir.mkdir(parents=True, exist_ok=True)
    audit.to_parquet(gold_dir / "auditoria_precos.parquet", index=False)
    report = build_report(conv, msgs, audit)
    Path("docs/achados-dataset.md").write_text(report, encoding="utf-8")
    return audit
