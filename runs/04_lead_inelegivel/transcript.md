# Cenário `04_lead_inelegivel`

Lead com 78 anos, acima do limite da tabela de aceitação. O agente confirma o dado (pode ser
erro de digitação), coleta o que falta para o especialista e encaminha (handoff `inelegivel`),
sem chamar a API e sem falar em recusa.

- Conversa: `conv_06320fe337ae`
- API: failure_rate=0.2, slow_rate=0.1
- Estágio final: `handoff`; desfecho: `inelegivel`
- Handoff: `inelegivel`
- Cotações: nenhuma

Mensagens do lead aparecem como foram gravadas: com os dados pessoais já mascarados.
O rastro completo (decisões, tentativas na API, handoff) está em `trace.txt`.

---
**Lead** (01:11:25.692): Olá, quero fazer o seguro do meu Compass 2022

**Agente** (01:11:28.838):  
Olá! Sou a assistente virtual da AutoSeguro, e vou te ajudar a cotar o seguro do seu Compass 2022 🚗 Para começar, me conta: qual a idade do motorista principal e o CEP de onde o carro dorme? Também preciso saber a partir de quando você quer o seguro.

**Lead** (01:11:28.842): tenho 78 anos

**Agente** (01:11:33.089):  
Obrigada! Só para confirmar: o motorista principal tem 78 anos, certo? Se estiver certo, pode me passar também o CEP de onde o carro dorme e a data em que você quer começar o seguro.

**Lead** (01:11:33.094): sim, 78

**Agente** (01:11:36.215):  
Perfeito, obrigada por confirmar! Agora preciso só do CEP de onde o Compass dorme para seguir com a cotação. Pode me enviar?

**Lead** (01:11:36.219): [CEP]

> *Handoff `ho_a6090b7c930a`: inelegivel (prioridade normal)*

**Agente** (01:11:37.665):  
Para o seu perfil, a cotação é feita por um especialista da nossa equipe. Já passei seus dados e ele te chama por aqui em breve.
