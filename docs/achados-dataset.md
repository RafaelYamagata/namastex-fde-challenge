# Achados do dataset de conversas

Gerado por `uv run autoseguro data silver && uv run autoseguro data audit`. Todos os números
saem da camada Silver (sem dados pessoais) e de cotações feitas na API oficial.

## Volume e qualidade

- 2.500 conversas, 26.470 mensagens.
- Conversas com timestamp voltando no tempo: 2.495
  (99,8%). A ordem confiável é `message_index`.
- A coluna `veiculo_texto` vem normalizada ("Marca Modelo Ano"), ao contrário do que o
  dicionário diz; o texto livre de verdade está no corpo das mensagens.
- Mensagens de mídia sem conteúdo: 774 documentos,
  550 imagens, 465 áudios
  (6,8% das mensagens).

## Dados pessoais

| Tipo | Conversas | % |
|---|---|---|
| cpf | 2.500 | 100,0% |
| email | 1.379 | 55,2% |
| telefone | 1.379 | 55,2% |
| placa | 839 | 33,6% |
| cep | 2.500 | 100,0% |
| nome | 2.500 | 100,0% |

- O nome do lead também aparece nas mensagens do vendedor (saudação): 2.752
  mensagens. Mascarar só as mensagens do lead não basta.
- O vendedor pede CPF em todas as conversas; a cotação não usa CPF.
- Depois do mascaramento, mensagens com dado pessoal ainda detectável: **0**.

## Auditoria de preço (vendedor × API oficial)

- Cotações refeitas na API: 2.500, com a taxa de falha padrão (20% de erro + 10% de
  lentidão). Na primeira passada, 22 ficaram sem
  resposta depois dos retries do cliente; novas rodadas (como o agente faz em segundo plano)
  deixaram 0 sem preço.
- **Elegíveis com preço oficial: 1.749.** Preço do vendedor igual ao oficial:
  **0**. Dentro de ±5%: 65
  (3,7%).
- Vendedor abaixo do oficial (mais de 5%): 1.198; acima: 486.
  Diferença mediana: -31,1%.
- **Recusados pela API: 751 (30,0%)**, e todos receberam
  preço do vendedor. Por motivo (o primeiro que a regra encontra): veiculo_recusado = 471, idade_recusada = 280.
- Taxa de fechamento (`ganho`): elegíveis 29,1%, recusados
  27,0% (203 conversas). O time humano fecha esses casos
  em ritmo parecido, por isso o agente os encaminha (handoff `inelegivel`) em vez de encerrar.
- Pré-checagem de elegibilidade do agente (tabela de `GET /planos`) × veredito da API:
  **2.500 de 2.500 concordam**.
- Leads em CEP de alto risco (agravo de 30%): 895 (35,8%). Sem
  CEP, a API não aplica o agravo; por isso o agente exige o CEP antes de cotar.

## Objeções × desfecho

| Objeção | ganho | em_negociacao | perdido | sem_resposta |
|---|---|---|---|---|
| (nenhuma) | 712 | 0 | 0 | 493 |
| concorrente | 0 | 114 | 83 | 0 |
| consultar_terceiro | 0 | 139 | 94 | 0 |
| franquia | 0 | 126 | 100 | 0 |
| pensar | 0 | 127 | 81 | 0 |
| preco | 0 | 251 | 180 | 0 |

## O que isso muda no agente

- O histórico não serve como fonte de preço nem de roteiro: os preços dos vendedores não
  seguem a regra. Preço só sai da `/quote`, e todo valor em R$ enviado ao lead é conferido.
- O agente não pede CPF, e-mail nem placa; o que chegar é mascarado antes do LLM e dos logs.
- Objeções de preço, franquia e concorrente encerram a maior parte das conversas perdidas ou
  paradas; o agente oferece uma alternativa de plano e, se não resolver, passa para um humano.
- Mídia (CNH, foto, áudio) aparece em boa parte das conversas: o agente não finge que leu;
  CNH é sinal de fechamento e vira handoff.
