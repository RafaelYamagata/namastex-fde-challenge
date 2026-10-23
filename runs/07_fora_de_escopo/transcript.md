# Cenário `07_fora_de_escopo`

O lead quer acionar o seguro por causa de uma batida (sinistro). Não é cotação: o agente
encaminha para o time certo em vez de improvisar.

- Conversa: `conv_430f07b95036`
- API: padrão
- Estágio final: `handoff`; desfecho: `fora_de_escopo`
- Handoff: `fora_de_escopo`
- Cotações: nenhuma

Mensagens do lead aparecem como foram gravadas: com os dados pessoais já mascarados.
O rastro completo (decisões, tentativas na API, handoff) está em `trace.txt`.

---
**Lead** (01:11:54.979): bati o carro ontem, como faço pra acionar o seguro?

> *Handoff `ho_d7d438dde038`: fora_de_escopo (prioridade normal)*

**Agente** (01:11:56.538):  
Esse assunto é tratado por outro time. Já encaminhei para um especialista, que te responde por aqui.
