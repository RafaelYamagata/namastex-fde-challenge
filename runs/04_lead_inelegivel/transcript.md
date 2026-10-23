# Cenário `04_lead_inelegivel`

Lead com 78 anos, acima do limite da tabela de aceitação. O agente confirma o dado (pode ser
erro de digitação), coleta o que falta para o especialista e encaminha (handoff `inelegivel`),
sem chamar a API e sem falar em recusa.

- Conversa: `conv_2c6459bdda6c`
- API: failure_rate=0.2, slow_rate=0.1
- Estágio final: `handoff`; desfecho: `inelegivel`
- Handoff: `inelegivel`
- Cotações: nenhuma

Mensagens do lead aparecem como foram gravadas: com os dados pessoais já mascarados.
O rastro completo (decisões, tentativas na API, handoff) está em `trace.txt`.

---
**Lead** (01:43:30.427): Olá, quero fazer o seguro do meu Compass 2022

**Agente** (01:43:33.758):  
Olá! Sou a assistente virtual da AutoSeguro e vou te ajudar a cotar o seguro do seu Compass 2022 🚗 Para começar, me conta: qual a idade do motorista principal, o CEP de onde o carro dorme e a partir de quando você quer o seguro?

**Lead** (01:43:33.761): tenho 78 anos

**Agente** (01:43:36.439):  
Obrigada por informar! Só para confirmar: o motorista principal tem 78 anos, correto? Se estiver certo, me passe também o CEP de onde o carro dorme e a data em que você quer começar o seguro.

**Lead** (01:43:36.446): sim, 78

**Agente** (01:43:40.186):  
Perfeito, obrigada por confirmar! Agora me passa o CEP de onde o Compass dorme, para eu seguir com a sua cotação 🚗

**Lead** (01:43:40.189): [CEP]

> *Handoff `ho_c390b87b6770`: inelegivel (prioridade normal)*

**Agente** (01:43:41.444):  
Para o seu perfil, a cotação é feita por um especialista da nossa equipe. Já passei seus dados e ele te chama por aqui em breve.
