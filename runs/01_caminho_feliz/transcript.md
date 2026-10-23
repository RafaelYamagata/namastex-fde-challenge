# Cenário `01_caminho_feliz`

Caminho feliz: o lead passa os dados aos poucos (e manda o CPF sem ninguém pedir), recebe a
cotação dos três planos, tira uma dúvida e fecha o Completo. O CEP é de região de risco
(agravo de 30%) e a vigência começa no meio do mês (primeiro pagamento proporcional). A API
roda com a instabilidade padrão (20% de erro + 10% de lentidão).

- Conversa: `conv_26be99f3400a`
- API: failure_rate=0.2, slow_rate=0.1, seed=1
- Estágio final: `handoff`; desfecho: `fechamento`
- Handoff: `fechamento`
- Cotações: `q_b0cb2049afa9` essencial: ok, `q_f0d9af1809e5` completo: ok, `q_07e967e84b0d` premium: ok

Mensagens do lead aparecem como foram gravadas: com os dados pessoais já mascarados.
O rastro completo (decisões, tentativas na API, handoff) está em `trace.txt`.

---
**Lead** (01:42:25.035): Oi, vi o anúncio de vocês, quanto fica o seguro?

**Agente** (01:42:29.138):  
Oi! Sou a assistente virtual da AutoSeguro 😊 O valor varia conforme a idade do motorista, a idade do veículo e a região do CEP, então preciso de alguns dados para cotar. Para começar, qual é o modelo e o ano do seu carro?

**Lead** (01:42:29.142): é um Onix 2021

**Agente** (01:42:31.882):  
Anotado: Onix 2021! Para eu cotar, me conta a idade do motorista principal, o CEP de onde o carro dorme e a partir de quando você quer o seguro.

**Lead** (01:42:31.887): tenho 35 anos, cep [CEP]

**Lead** (01:42:31.888): meu cpf é [CPF] se precisar

**Agente** (01:42:34.845):  
Obrigada pelas informações! Não precisa do CPF para a cotação, então fique tranquilo(a). Só falta me dizer: a partir de quando você quer começar o seguro?

**Lead** (01:42:34.849): queria que começasse dia 15

**Agente** (01:42:36.148):  
Só um instante, estou calculando sua cotação no sistema... ⏳

**Agente** (01:42:38.901):  
Suas cotações:  
  
*Essencial*: R$ 155,87/mês  
Coberturas: colisão, roubo e furto  
Franquia: R$ 4.500,00  
1º pagamento: R$ 83,13 (proporcional a 16 de 30 dias)  
  
*Completo*: R$ 272,87/mês  
Coberturas: colisão, roubo, furto, danos a terceiros e vidros  
Franquia: R$ 3.000,00  
1º pagamento: R$ 145,53 (proporcional a 16 de 30 dias)  
  
*Premium*: R$ 441,87/mês  
Coberturas: colisão, roubo, furto, danos a terceiros, vidros, carro reserva e assistência 24h  
Franquia: R$ 1.500,00  
1º pagamento: R$ 235,66 (proporcional a 16 de 30 dias)  
  
Início da vigência: 15/11/2026. Como começa no meio do mês, o 1º pagamento cobre só os dias restantes de novembro; depois, mensalidade cheia. Carência: roubo e furto passam a valer 30 dias após o início da vigência.  
  
Qual deles faz mais sentido pra você? Posso explicar as diferenças.  
  
Ref.: q_b0cb2049afa9, q_f0d9af1809e5, q_07e967e84b0d

**Lead** (01:42:38.905): qual a diferença de franquia entre eles?

**Agente** (01:42:44.065):  
A franquia é o valor que você paga em caso de sinistro com danos ao próprio carro: no Essencial é R$ 4.500,00, no Completo R$ 3.000,00 e no Premium R$ 1.500,00. Quanto menor a franquia, maior a mensalidade. Quer seguir com algum desses planos?

**Lead** (01:42:44.068): fechado, vou de completo!

> *Handoff `ho_20f304c18105`: fechamento (prioridade alta)*

**Agente** (01:42:45.569):  
Ótima escolha! 🎉 Para emitir a apólice, um especialista vai finalizar com você por aqui: ele confirma seus dados e envia a forma de pagamento.
