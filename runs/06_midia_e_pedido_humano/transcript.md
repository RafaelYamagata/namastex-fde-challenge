# Cenário `06_midia_e_pedido_humano`

O lead manda um áudio (o agente pede texto, sem fingir que ouviu) e depois pede para falar com
uma pessoa: handoff imediato. A mensagem seguinte recebe um único aviso; depois disso o agente
fica em silêncio, porque a conversa é do humano.

- Conversa: `conv_5e2e3acd967b`
- API: padrão
- Estágio final: `handoff`; desfecho: `pedido_humano`
- Handoff: `pedido_humano`
- Cotações: nenhuma

Mensagens do lead aparecem como foram gravadas: com os dados pessoais já mascarados.
O rastro completo (decisões, tentativas na API, handoff) está em `trace.txt`.

---
**Lead** (01:43:52.433): [audio] mensagem de voz (18s)

**Agente** (01:43:53.929):  
Olá! Sou a assistente virtual da AutoSeguro 😊 Por aqui eu só consigo ler texto, então não consegui ouvir seu áudio. Pode me escrever o que você precisa para eu te ajudar com a cotação do seu seguro auto?

**Lead** (01:43:53.933): prefiro falar com uma pessoa, pode ser?

> *Handoff `ho_228d83c6c5a8`: pedido_humano (prioridade normal)*

**Agente** (01:43:55.222):  
Claro! Vou te passar para um dos nossos especialistas. Ele continua o atendimento por aqui mesmo, em instantes.

**Lead** (01:43:55.228): oi?

**Agente** (01:43:55.231):  
Um especialista já vai te responder por aqui. 🙂

**Lead** (01:43:55.234): alguém aí?
