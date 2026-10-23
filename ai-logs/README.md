# Conversas com IA

O projeto inteiro foi feito com o **Claude Code** (modelo Claude Opus 5.5), numa sessão
contínua: exploração do repositório, plano, implementação, testes, execução dos cenários e
documentação.

| Arquivo | O que é |
|---|---|
| `claude-code/73c985c6-....jsonl` | A sessão de trabalho, completa: mensagens, raciocínio visível, chamadas de ferramentas e resultados |
| `claude-code/f59dd51c-....jsonl` | Sessão curta de configuração (login), sem conteúdo do desafio |

Os arquivos são o `.jsonl` cru que o Claude Code grava em `~/.claude/projects/`, exportados por
[`scripts/export_ai_logs.py`](../scripts/export_ai_logs.py), que remove os valores do `.env`
(chave da API, workspace, chave do HMAC), qualquer coisa com cara de token, o e-mail do autor
e o caminho da home. O script falha se algum desses valores sobrar.

Os dados pessoais que aparecem nas saídas de ferramentas (CPFs, e-mails, telefones) vêm do
dataset do desafio, que é sintético e já está no repositório.

Para ler: cada linha é um evento JSON. As mensagens do usuário têm `"type": "user"` e as
respostas `"type": "assistant"`; o fio da conversa segue `parentUuid`.
