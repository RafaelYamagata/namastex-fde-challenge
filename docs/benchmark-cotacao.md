# Benchmark do cliente de cotação

Gerado por `uv run autoseguro bench --n 200` (serviço real, `QUOTE_SLOW_RATE=0.1`, `QUOTE_SEED=42`, 8 chamadas concorrentes).
O cliente "ingênuo" faz uma única chamada com timeout de 30s.

| Taxa de falha | Cliente | Com preço | Sem preço | Tentativas (média) | Espera p50 | Espera p95 | Espera máx |
|---|---|---|---|---|---|---|---|
| 20% | resiliente | 196/200 (98.0%) | 4 | 1.51 | 0.00s | 3.01s | 8.17s |
| 20% | ingênuo | 163/200 (81.5%) | 37 | 1.00 | 0.00s | 8.01s | 8.03s |
| 50% | resiliente | 183/200 (91.5%) | 17 | 2.12 | 0.15s | 3.35s | 6.59s |
| 50% | ingênuo | 97/200 (48.5%) | 103 | 1.00 | 0.00s | 8.01s | 8.02s |
| 100% | resiliente | 0/200 (0.0%) | 200 | 0.05 | 0.00s | 0.00s | 0.26s |
| 100% | ingênuo | 0/200 (0.0%) | 200 | 1.00 | 0.00s | 0.01s | 0.02s |
