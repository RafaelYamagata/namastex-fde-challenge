# Plano — Agente de cotação AutoSeguro

Documento de trabalho: as decisões e o escopo combinados antes de começar a implementar.
O README final explica o resultado; este arquivo registra o caminho.

> **Status:** executado. Mudanças em relação a este plano: o modelo passou a ser o Claude
> Sonnet 5.5 com esforço baixo (decisão do autor, por latência); o circuit breaker passou de
> "N falhas seguidas" para taxa de falha numa janela (calibrado para os ~30% de falha normal da
> API); e entrou um relógio de negócio no fuso de Brasília (bug visto ao rodar no Docker).

**Prazo:** entrega em 23/10/2026. O enunciado fala em ~3 dias, mas o prazo combinado é
de ~1 dia, então o escopo foi comprimido (ver [Fora do escopo](#fora-do-escopo-e-por-quê)).

## Decisões

| Tema | Decisão |
|---|---|
| LLM | Anthropic (Claude), chave em `.env` (nunca commitado) |
| Idioma | Docs em português; código em inglês, mantendo os termos de domínio da API (`plano_id`, `franquia`, `carencia`) |
| `quote-service/` | Não é alterado: é o "sistema legado" que não é nosso. Contornamos as limitações dele no cliente |
| Leads inelegíveis | Handoff para humano: ver seção [Leads inelegíveis](#leads-inelegíveis) |

## Achados que guiam o design

**API de cotação**

- Só a `/quote` é instável: 20% das chamadas devolvem 500/502/503 e 10% travam por 8s
  (e depois respondem com sucesso).
- Dois erros diferentes usam HTTP 422: recusa de negócio (`{"error":"cotacao_recusada"}`)
  e erro de validação do FastAPI (`{"detail":[...]}`). Nenhum dos dois deve ser repetido.
- A API erra o preço em silêncio em alguns casos:
  - `cep` é opcional; sem ele, o agravo de região de risco (×1,30) não é aplicado.
    36% dos leads do dataset moram em regiões de risco.
  - A região é lida dos 2 primeiros caracteres da string enviada; um CEP sem o zero à
    esquerda (`7123-456`) cai na região errada.
  - `plano_id` vira `essencial` por padrão, e `data_inicio` no passado é aceita.
  - Idade do veículo = `ano atual - ano do veículo`; um carro ano-modelo 2027 comprado em
    2026 é recusado ("fora das faixas").
- Carência de 30 dias para roubo/furto e primeiro pagamento proporcional precisam ser
  informados ao lead.

**Dataset (2.500 conversas, 26.470 mensagens)**

- 2.495 conversas têm timestamps fora de ordem: a ordem válida é `message_index`.
- Dados pessoais: CPF em 100% das conversas, e-mail e telefone em 55%, placa em 34%,
  CEP em 100%. O nome do lead aparece em `sender_name` e também na saudação do vendedor.
- ~7% das mensagens são mídia sem conteúdo (CNH, fotos, áudios).
- Recalculando as cotações dos vendedores com as regras oficiais: 0 de 1.749 batem com o
  preço oficial, a mediana fica 31% abaixo, e 751 leads (30%) são inelegíveis pelas regras
  mas receberam preço mesmo assim (203 marcados como `ganho`).
- Conclusão: o histórico não serve como fonte de preço nem de roteiro. Serve para personas
  de teste, padrões de objeção, tratamento de dados pessoais e avaliação.

## Arquitetura: "o LLM conversa, o código decide"

```
mensagem ─► ingestão (id, deduplicação, mascaramento de dados pessoais)
         ─► NLU [LLM, saída estruturada]  → dados do lead + intenções
         ─► motor de regras [Python puro] → normaliza/valida dados, elegibilidade,
                                             regras de handoff, próxima ação
         ─► ação: perguntar | cotar | apresentar | responder | alternativa | adiar | handoff
         ─► NLG [LLM]; mensagens com preço vêm de template preenchido com o JSON da API
         ─► guardrail de saída: todo R$ precisa vir de uma cotação desta conversa
         ─► resposta (cada passo gravado como evento, com id e status)
```

## Política de handoff

| Código | Gatilho | Por quê |
|---|---|---|
| `pedido_humano` | Lead pede uma pessoa | Sempre atendido, na hora |
| `fechamento` | "fechado", "pode emitir", envio de CNH | Emissão exige CPF, documentos e pagamento. Lead quente, prioridade máxima |
| `negociacao` | Objeção de preço/concorrente persiste após 1 alternativa de plano | O agente não tem alçada de desconto |
| `inelegivel` | Regras recusam o perfil (idade fora de 18–75, veículo com mais de 20 anos, ano-modelo futuro), após confirmar o dado | A API não cota, mas o time humano fecha esses casos no histórico |
| `cotacao_indisponivel` | `/quote` segue falhando após as novas tentativas em segundo plano | Nunca inventar preço |
| `fora_de_escopo` | Sinistro, cancelamento, outros produtos, uso para app | Não é papel do agente |
| `sem_progresso` | 3 turnos sem informação nova, contradições, mídia ilegível | Evitar loop |
| `atrito` | Reclamação, irritação, ameaça jurídica | Exige julgamento humano |

Resolve sozinho: coleta de dados, cotação, explicação de coberturas/franquia/carência/
pro-rata, comparação e recotação de planos, "preciso pensar" (guarda a cotação). Depois de
um handoff, o agente para de responder.

## Leads inelegíveis

São os leads que as regras de `GET /planos` recusam: idade acima de 75 ou abaixo de 18,
veículo com mais de 20 anos (em 2026, ano 2005 ou anterior) e ano-modelo futuro (lacuna da
regra da API). No dataset são 751 leads (30%).

**Decisão: handoff para humano.** O dataset (tratado como fonte da verdade neste exercício)
mostra vendedores fechando negócio com esses leads: 203 de 751 marcados como `ganho`, taxa
de 27%, perto dos 29% dos elegíveis. Ou seja, o time humano tem meios de cotar esses casos
que a API não tem.

1. Checar elegibilidade assim que idade e ano do veículo forem conhecidos, usando a tabela
   de `GET /planos`.
2. Confirmar o dado uma vez (um "76" pode ser um "67" digitado errado).
3. Seguir coletando o que falta (CEP, plano de interesse), para o especialista receber o
   caso completo.
4. Não chamar a `/quote` (a recusa já é conhecida) e não mostrar preço. Avisar que a
   cotação para esse perfil é feita por um especialista, sem linguagem de "recusa".
5. Handoff `inelegivel`, prioridade normal, com o motivo da regra no resumo.

## Falhas da `/quote`

- Validar antes de chamar (CEP com 8 dígitos obrigatório, data ≥ hoje, `plano_id` explícito).
- Timeout de leitura ~2,5s; nova tentativa só em 5xx, timeout e erro de conexão; até 4
  tentativas com backoff exponencial e jitter, dentro de ~10s.
- Circuit breaker compartilhado entre conversas; cache por (payload, data).
- Se esgotar: mensagem honesta, sem preço aproximado; a cotação entra numa fila com id e é
  retentada em segundo plano; o resultado é enviado ao lead quando sair. Passou de ~5 min,
  handoff `cotacao_indisponivel`.
- O LLM também falha: retries do SDK, depois mensagem padrão, depois handoff `erro_interno`.

## Rastreabilidade

SQLite só de inserção: mensagens, cotações (pendente → ok/recusada/adiada/falhou),
tentativas (status HTTP, latência, tipo de erro), handoffs e mudanças de estado, todos com
id. Logs JSON com os mesmos ids. `autoseguro trace <conversation_id>` mostra a linha do tempo.

## Dados sensíveis (LGPD)

- O agente nunca pede CPF, e-mail ou placa (a cotação só precisa de idade, ano e CEP).
- O que o lead mandar por conta própria é mascarado antes de ir para o LLM e para os logs.
  Logs guardam o CEP só com os 2 primeiros dígitos.
- Camada Silver do dataset: identificadores viram hashes com chave (estáveis, não
  reversíveis), nomes mascarados também dentro do texto das mensagens.
- `ai-logs/` passa por um script que remove segredos e dados pessoais do autor.

## Avaliação

- Testes unitários sem rede e sem LLM: política de retry, circuit breaker, mascaramento,
  normalização, regras de handoff, guardrail de preço.
- Medição de resiliência contra a API real com `QUOTE_SEED`, em taxas de falha 0,2 / 0,5 / 1,0.
- Cenários com o LLM de verdade, verificando desfechos (não o texto exato).
- `runs/`: log de uma conversa completa com cotação, mais uma rodada com a API fora do ar.

## Cronograma

| Bloco | Entrega |
|---|---|
| 22/10, noite | Estrutura do projeto, cliente da cotação + testes + medição de resiliência, mascaramento, camada Silver + achados |
| 23/10, manhã | Núcleo do agente: NLU/NLG, motor de regras, guardrails, eventos, CLI de chat e `trace` |
| 23/10, meio do dia | Cenários e logs em `runs/` |
| 23/10, tarde | README (como rodar + decisões), serviço do agente no `docker-compose`, `ai-logs/`, revisão final, repo público |

## Fora do escopo (e por quê)

- Webhook no formato do WhatsApp (agrupar rajadas de mensagens, ignorar duplicadas):
  descrito no README como próximo passo.
- Requisição duplicada ("hedging") para cortar a latência da chamada lenta: o retry com
  timeout já resolve; fica como melhoria.
- Fila durável para cotações adiadas: a versão entregue retenta dentro do processo.
- Avaliação replicando centenas de conversas do dataset: só se sobrar tempo, numa amostra.
