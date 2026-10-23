# Cenário `05_objecao_e_negociacao`

O lead pede o plano Completo e acha caro. O agente cota uma alternativa mais barata
(Essencial), explicando a troca; o lead diz que a concorrente cobra menos e o agente passa
para um humano, que tem alçada para negociar.

- Conversa: `conv_6164f3812fdd`
- API: failure_rate=0.2, slow_rate=0.1, seed=5
- Estágio final: `handoff`; desfecho: `negociacao`
- Handoff: `negociacao`
- Cotações: `q_0921830882fe` completo: ok, `q_ea22de5435d2` essencial: ok

Mensagens do lead aparecem como foram gravadas: com os dados pessoais já mascarados.
O rastro completo (decisões, tentativas na API, handoff) está em `trace.txt`.

---
**Lead** (01:11:38.371): oi, quero cotar o plano completo pro meu Kwid 2015

**Agente** (01:11:41.802):  
Olá! Sou a assistente virtual da AutoSeguro e vou te ajudar com a cotação do plano Completo para o seu Kwid 2015. 🚗 Para começar, me conta: qual a idade do motorista principal, o CEP de onde o carro dorme e a partir de quando você quer o seguro?

**Lead** (01:11:41.807): 31 anos, cep [CEP], pode ser a partir de amanhã

**Agente** (01:11:43.203):  
Só um instante, estou calculando sua cotação no sistema... ⏳

**Agente** (01:11:43.215):  
Sua cotação:  
  
*Completo*: R$ 395,66/mês  
Coberturas: colisão, roubo, furto, danos a terceiros e vidros  
Franquia: R$ 3.000,00  
1º pagamento: R$ 114,87 (proporcional a 9 de 31 dias)  
  
Início da vigência: 23/10/2026. Como começa no meio do mês, o 1º pagamento cobre só os dias restantes de outubro; depois, mensalidade cheia. Carência: roubo e furto passam a valer 30 dias após o início da vigência.  
  
Quer seguir com esse plano?  
  
Ref.: q_[TELEFONE]fe

**Lead** (01:11:43.219): nossa, achei caro

**Agente** (01:11:46.531):  
Entendo, o valor pesa mesmo no orçamento. Posso te mostrar o plano Essencial como alternativa, com mensalidade menor: ele cobre colisão, roubo e furto, mas não inclui danos a terceiros nem vidros, e a franquia sobe de R$ 3.000,00 para R$ 4.500,00. Quer que eu faça essa cotação?

**Agente** (01:11:46.541):  
Sua cotação:  
  
*Essencial*: R$ 226,01/mês  
Coberturas: colisão, roubo e furto  
Franquia: R$ 4.500,00  
1º pagamento: R$ 65,62 (proporcional a 9 de 31 dias)  
  
Início da vigência: 23/10/2026. Como começa no meio do mês, o 1º pagamento cobre só os dias restantes de outubro; depois, mensalidade cheia. Carência: roubo e furto passam a valer 30 dias após o início da vigência.  
  
Quer seguir com esse plano?  
  
Ref.: q_ea22de5435d2

**Lead** (01:11:46.546): a Porto Seguro me ofereceu menos

> *Handoff `ho_36d80290be45`: negociacao (prioridade normal)*

**Agente** (01:11:48.170):  
Entendo. Vou chamar um especialista que pode avaliar outras condições pra você. Ele te responde por aqui.
