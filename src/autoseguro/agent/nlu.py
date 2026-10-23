"""Understanding a lead's message: structured extraction with the LLM, validated by code.

The LLM only *proposes* values and intents. Ranges, dates and plan ids are checked here, and
anything implausible is dropped (the agent will simply ask again). The LLM never sees raw
personal data: messages arrive masked, and the CEP is captured by regex, not by the model.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field

from autoseguro.agent.llm import LLM
from autoseguro.agent.state import ConversationState, Intent
from autoseguro.quote.models import PLAN_IDS

WEEKDAYS = [
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
]


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


NLU_SCHEMA = {
    "type": "object",
    "properties": {
        "idade": _nullable({"type": "integer"}),
        "veiculo_ano": _nullable({"type": "integer"}),
        "veiculo_modelo": _nullable({"type": "string"}),
        "plano_id": _nullable({"type": "string", "enum": list(PLAN_IDS)}),
        "data_inicio": _nullable({"type": "string"}),
        "cep_invalido": {"type": "boolean"},
        "intencoes": {
            "type": "array",
            "items": {"type": "string", "enum": [i.value for i in Intent]},
        },
        "pergunta": _nullable({"type": "string"}),
    },
    "required": [
        "idade",
        "veiculo_ano",
        "veiculo_modelo",
        "plano_id",
        "data_inicio",
        "cep_invalido",
        "intencoes",
        "pergunta",
    ],
    "additionalProperties": False,
}

NLU_SYSTEM = """\
Você analisa mensagens de WhatsApp de leads da AutoSeguro, uma seguradora que vende seguro \
auto, e preenche o esquema JSON pedido. Responda apenas com o JSON.

Extração (use null para o que não foi informado):
- Extraia só o que o lead disse nas MENSAGENS NOVAS. Use a conversa recente apenas para \
entender a que as respostas se referem (se o agente perguntou a idade e o lead respondeu \
"42", idade = 42). Se o lead corrigir um dado, devolva o valor novo.
- idade: idade do motorista em anos. Se vier data de nascimento, calcule com a data de hoje.
- veiculo_ano: ano do veículo com 4 dígitos ("15" = 2015; "0km" = ano atual). Se vier \
"fabricação/modelo" (ex.: 2025/2026), use o ano-modelo.
- veiculo_modelo: marca/modelo como o lead escreveu (ex.: "Onix", "Toyota Corolla").
- plano_id: só se o lead escolher ou pedir um plano: essencial, completo ou premium. \
"O mais completo/top" = premium; "o mais barato/básico" = essencial.
- data_inicio: quando o lead quer que o seguro comece, em AAAA-MM-DD, resolvendo \
expressões relativas a partir de hoje. "Hoje", "agora", "o quanto antes", "tanto faz" = \
hoje. "Dia 15" = o próximo dia 15 a partir de hoje.
- cep_invalido: true se o lead tentou informar um CEP com número de dígitos diferente de 8. \
CEPs válidos aparecem como [CEP]: o sistema já os capturou.
- [CPF], [EMAIL], [TELEFONE], [PLACA], [NOME] e [CEP] são dados pessoais removidos por \
privacidade. Ignore-os.

Intenções (zero ou mais):
- saudacao: cumprimento sem outro conteúdo.
- pedir_humano: quer falar com uma pessoa, atendente ou corretor.
- quer_fechar: quer contratar, fechar ou emitir ("fechado", "pode emitir", "quero esse").
- objecao_preco: acha caro ou pede desconto.
- objecao_franquia: acha a franquia alta.
- concorrente: diz que outra seguradora ofereceu menos.
- vai_pensar: vai pensar, consultar alguém ou responder depois.
- desistir: não tem mais interesse.
- duvida: pergunta sobre planos, coberturas, franquia, carência, pagamento ou o processo.
- outro_plano: pede cotação ou detalhes de um plano diferente do apresentado.
- reenviar_cotacao: pede para ver os valores de novo.
- confirma: diz "sim" ou confirma uma informação que o agente perguntou.
- nega: diz "não" ou que a informação perguntada está errada.
- fora_de_escopo: assunto que não é cotação de seguro auto: sinistro, cancelamento, \
segunda via, outro tipo de seguro, carro usado para aplicativo ou trabalho, moto, caminhão.
- reclamacao: reclamação, irritação ou ameaça (Procon, processo).

pergunta: se houver a intenção duvida, a pergunta do lead resumida em uma frase; senão null.
"""


@dataclass
class NluResult:
    idade: int | None = None
    veiculo_ano: int | None = None
    veiculo_modelo: str | None = None
    plano_id: str | None = None
    data_inicio: dt.date | None = None
    cep_invalido: bool = False
    intents: set[Intent] = field(default_factory=set)
    pergunta: str | None = None
    dropped: list[str] = field(default_factory=list)  # proposed values that failed validation


def build_user_prompt(state: ConversationState, new_messages: list[str], today: dt.date) -> str:
    d = state.data
    known = {
        "idade": d.idade,
        "veiculo_ano": d.veiculo_ano,
        "veiculo_modelo": d.veiculo_modelo,
        "cep": "informado" if d.cep else None,
        "plano_id": d.plano_id,
        "data_inicio": d.data_inicio.isoformat() if d.data_inicio else None,
    }
    recent = "\n".join(f"{role}: {text}" for role, text in state.transcript[-8:]) or "(início)"
    new = "\n".join(f"- {m}" for m in new_messages)
    return (
        f"Hoje: {today.isoformat()} ({WEEKDAYS[today.weekday()]})\n"
        f"Dados já conhecidos: {json.dumps(known, ensure_ascii=False)}\n\n"
        f"Conversa recente:\n{recent}\n\n"
        f"MENSAGENS NOVAS DO LEAD:\n{new}"
    )


def parse(raw: dict, today: dt.date) -> NluResult:
    result = NluResult(cep_invalido=bool(raw.get("cep_invalido")), pergunta=raw.get("pergunta"))
    for name in raw.get("intencoes") or []:
        try:
            result.intents.add(Intent(name))
        except ValueError:
            result.dropped.append(f"intencao:{name}")

    idade = raw.get("idade")
    if isinstance(idade, int) and 0 <= idade <= 120:
        result.idade = idade
    elif idade is not None:
        result.dropped.append(f"idade:{idade}")

    ano = raw.get("veiculo_ano")
    if isinstance(ano, int) and 1950 <= ano <= today.year + 2:
        result.veiculo_ano = ano
    elif ano is not None:
        result.dropped.append(f"veiculo_ano:{ano}")

    modelo = raw.get("veiculo_modelo")
    if isinstance(modelo, str) and modelo.strip():
        result.veiculo_modelo = modelo.strip()[:60]

    plano = raw.get("plano_id")
    if plano in PLAN_IDS:
        result.plano_id = plano

    inicio = raw.get("data_inicio")
    if inicio:
        try:
            parsed = dt.date.fromisoformat(inicio)
            if today <= parsed <= today + dt.timedelta(days=365):
                result.data_inicio = parsed
            else:
                result.dropped.append(f"data_inicio:{inicio}")
        except ValueError:
            result.dropped.append(f"data_inicio:{inicio}")
    return result


def understand(
    llm: LLM, state: ConversationState, new_messages: list[str], today: dt.date
) -> NluResult:
    text = llm.complete(
        system=NLU_SYSTEM,
        user=build_user_prompt(state, new_messages, today),
        schema=NLU_SCHEMA,
        purpose="nlu",
        conversation_id=state.conversation_id,
    )
    return parse(json.loads(text), today)
