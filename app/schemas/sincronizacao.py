import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.enums import NivelRisco
from app.schemas.atendimento import PacienteIn, ResultadoInferenciaIn


class PacienteSync(PacienteIn):
    """Paciente com id gerado no celular (o app pode criar o paciente estando offline)."""

    id: uuid.UUID


class AtendimentoSync(BaseModel):
    """Um atendimento como ele está guardado no celular do ACS."""

    id: uuid.UUID  # UUID gerado no celular: é ele que impede duplicidade no reenvio
    paciente: PacienteSync
    data_hora: datetime
    atualizado_em: datetime  # última alteração no celular (usado na regra RN06)

    relato_texto: str | None = None
    relato_voz_path: str | None = Field(default=None, max_length=255)
    imagem_exantema_path: str | None = Field(default=None, max_length=255)

    # RN05: geolocalização obrigatória
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    rua: str | None = Field(default=None, max_length=150)
    bairro: str | None = Field(default=None, max_length=100)
    municipio: str | None = Field(default=None, max_length=100)

    nivel_risco: NivelRisco | None = None
    resultado: ResultadoInferenciaIn | None = None

    @field_validator("data_hora", "atualizado_em")
    @classmethod
    def exige_fuso_horario(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Informe o fuso horário, por exemplo 2026-10-03T10:30:00-04:00.")
        return v


_EXEMPLO = {
    "atendimentos": [
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "paciente": {
                "id": "22222222-2222-4222-8222-222222222222",
                "nome": "Maria da Silva",
                "sexo": "F",
            },
            "data_hora": "2026-10-03T10:30:00-04:00",
            "atualizado_em": "2026-10-03T10:35:00-04:00",
            "relato_texto": "Febre alta e dor atrás dos olhos",
            "latitude": -3.119,
            "longitude": -60.0217,
            "municipio": "Manaus",
            "resultado": {
                "score_probabilidade": 0.82,
                "sinais_alarme": ["dor abdominal intensa"],
                "recomendacao": "Encaminhar para a UBS",
            },
        }
    ]
}


class SincronizacaoIn(BaseModel):
    """Lote de atendimentos pendentes enviado pelo celular.

    Cada item é validado separadamente: um registro com erro não derruba o lote.
    O formato de cada item é o de AtendimentoSync (veja o exemplo).
    """

    atendimentos: list[dict[str, Any]] = Field(min_length=1, max_length=100)

    model_config = {"json_schema_extra": {"examples": [_EXEMPLO]}}


class SituacaoSync(str, Enum):
    CRIADO = "CRIADO"
    ATUALIZADO = "ATUALIZADO"
    IGNORADO_DESATUALIZADO = "IGNORADO_DESATUALIZADO"
    IGNORADO_PARECER_MEDICO = "IGNORADO_PARECER_MEDICO"
    REJEITADO = "REJEITADO"


class ItemSync(BaseModel):
    id: uuid.UUID | None = None
    situacao: SituacaoSync
    detalhe: str | None = None


class SincronizacaoOut(BaseModel):
    # IDs que o celular já pode marcar como SINCRONIZADO (tudo, menos os REJEITADOS)
    ids_sincronizados: list[uuid.UUID]
    itens: list[ItemSync]
    criados: int
    atualizados: int
    ignorados: int
    rejeitados: int
