# Cenário `06_midia_e_pedido_humano`

O lead manda um áudio (o agente pede texto, sem fingir que ouviu) e depois pede para falar com
uma pessoa: handoff imediato. A mensagem seguinte recebe um único aviso; depois disso o agente
fica em silêncio, porque a conversa é do humano.

- Conversa: `conv_df5a00ea79ad`
- API: padrão
- Estágio final: `handoff`; desfecho: `pedido_humano`
- Handoff: `pedido_humano`
- Cotações: nenhuma

Mensagens do lead aparecem como foram gravadas: com os dados pessoais já mascarados.
O rastro completo (decisões, tentativas na API, handoff) está em `trace.txt`.

---
**Lead** (01:11:48.877): [audio] mensagem de voz (18s)

**Agente** (01:11:52.920):  
Olá! Sou a assistente virtual da AutoSeguro e vou te ajudar com a cotação do seu seguro auto 🙂 Por aqui eu só consigo ler mensagens de texto, então não consegui ouvir o seu áudio. Pode me escrever o que você precisa?

**Lead** (01:11:52.924): prefiro falar com uma pessoa, pode ser?

> *Handoff `ho_856991f4017f`: pedido_humano (prioridade normal)*

**Agente** (01:11:54.295):  
Claro! Vou te passar para um dos nossos especialistas. Ele continua o atendimento por aqui mesmo, em instantes.

**Lead** (01:11:54.303): oi?

**Agente** (01:11:54.306):  
Um especialista já vai te responder por aqui. 🙂

**Lead** (01:11:54.309): alguém aí?
