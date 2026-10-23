# Cenário `03_api_cai_e_volta`

A API está fora do ar na hora da cotação e volta logo depois. O lead recebe o aviso honesto e,
quando a API responde, a cotação chega sozinha (mensagem proativa, rodada em segundo plano).
Depois o lead diz que vai pensar: o agente guarda a cotação, sem handoff.

- Conversa: `conv_39485048fb0c`
- API: failure_rate=1.0, slow_rate=0.0, deferred_schedule=[5, 15, 30]
- Estágio final: `cotado`; desfecho: `em_negociacao`
- Handoff: nenhum
- Cotações: `q_f217d82499f1` premium: ok

Mensagens do lead aparecem como foram gravadas: com os dados pessoais já mascarados.
O rastro completo (decisões, tentativas na API, handoff) está em `trace.txt`.

---
**Lead** (01:11:10.194): oi! quero cotar o seguro do meu Corolla 2018, só o plano premium

**Agente** (01:11:14.009):  
Olá! Sou a assistente virtual da AutoSeguro e vou te ajudar a cotar o plano Premium para o seu Corolla 2018 🙂 Para começar, me conta a idade do motorista principal, o CEP de onde o carro dorme e a partir de quando você quer o seguro?

**Lead** (01:11:14.013): tenho 29 anos

**Lead** (01:11:14.015): cep [CEP], começando dia 1º do mês que vem

**Agente** (01:11:15.600):  
Só um instante, estou calculando sua cotação no sistema... ⏳

**Agente** (01:11:16.911):  
Nosso sistema de cotação está instável agora e não consegui calcular a cotação. Não vou te passar um valor aproximado pra não te dar informação errada: assim que o sistema responder, te mando o valor certinho aqui.

**Agente** (01:11:21.930):  
Saiu! Aqui está a cotação que estava pendente:  
  
*Premium*: R$ 488,61/mês  
Coberturas: colisão, roubo, furto, danos a terceiros, vidros, carro reserva e assistência 24h  
Franquia: R$ 1.500,00  
  
Início da vigência: 01/11/2026. Carência: roubo e furto passam a valer 30 dias após o início da vigência.  
  
Quer seguir com esse plano?  
  
Ref.: q_f217d82499f1

**Lead** (01:11:21.935): vou pensar e te falo

**Agente** (01:11:24.995):  
Sem problema, pode pensar com calma! Sua cotação do Premium fica registrada aqui, mas vale lembrar que os valores podem mudar com o tempo. Quando quiser continuar, é só me chamar por aqui 🙂
