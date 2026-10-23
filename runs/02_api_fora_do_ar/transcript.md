# Cenário `02_api_fora_do_ar`

A API de cotação cai de vez (100% de falha). O agente tenta, avisa o lead sem inventar preço,
segue tentando em segundo plano (o circuit breaker evita martelar a API) e, quando o prazo
acaba, passa para um humano com prioridade alta.

- Conversa: `conv_05947ae321b4`
- API: failure_rate=1.0, slow_rate=0.0, deferred_schedule=[3, 6, 12]
- Estágio final: `handoff`; desfecho: `cotacao_indisponivel`
- Handoff: `cotacao_indisponivel`
- Cotações: `q_39dcf312edf4` essencial: unavailable, `q_50bb245e22d6` completo: unavailable, `q_ce831ef24d4b` premium: unavailable

Mensagens do lead aparecem como foram gravadas: com os dados pessoais já mascarados.
O rastro completo (decisões, tentativas na API, handoff) está em `trace.txt`.

---
**Lead** (01:42:46.296): boa tarde, quero uma cotação pro meu HB20 2019

**Agente** (01:42:49.909):  
Boa tarde! Sou a assistente virtual da AutoSeguro e vou te ajudar com a cotação do seu HB20 2019 🙂 Para começar, me conta a idade do motorista principal, o CEP de onde o carro dorme e a partir de quando você quer o seguro.

**Lead** (01:42:49.912): 42 anos, cep [CEP], pode começar hoje

**Agente** (01:42:51.257):  
Só um instante, estou calculando sua cotação no sistema... ⏳

**Agente** (01:42:51.951):  
Nosso sistema de cotação está instável agora e não consegui calcular a cotação. Não vou te passar um valor aproximado pra não te dar informação errada: assim que o sistema responder, te mando o valor certinho aqui.

**Lead** (01:42:51.954): e aí, conseguiu?

> *Handoff `ho_b003a01a098c`: cotacao_indisponivel (prioridade alta)*

**Agente** (01:43:12.976):  
Nosso sistema de cotação continua instável e não quero te deixar esperando. Passei seu pedido para um especialista, que te envia os valores por aqui.
