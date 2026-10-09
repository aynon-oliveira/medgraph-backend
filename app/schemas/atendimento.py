import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import NivelRisco, StatusSincronizacao, StatusValidacao


def normalizar_sintomas(valores: list[str]) -> list[str]:
    """Padroniza os nomes (minúsculas, sem espaços sobrando, sem repetição) para o grafo."""
    vistos: list[str] = []
    for valor in valores:
        nome = " ".join(valor.strip().lower().split())[:80]
        if nome and nome not in vistos:
            vistos.append(nome)
    return vistos


class PacienteIn(BaseModel):
    nome: str = Field(min_length=2, max_length=150)
    data_nascimento: date | None = None
    sexo: Literal["M", "F", "O"] | None = None


class PacienteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nome: str
    data_nascimento: date | None = None
    sexo: str | None = None


class ResultadoInferenciaIn(BaseModel):
    """Saída do motor de IA, calculada no celular e enviada junto com o atendimento."""

    score_probabilidade: float = Field(ge=0, le=1)
    sinais_alarme: list[str] = Field(default_factory=list)
    recomendacao: str | None = None
    modelo_versao: str | None = Field(default=None, max_length=60)  # qual modelo/regra gerou o risco


class ResultadoInferenciaOut(ResultadoInferenciaIn):
    model_config = ConfigDict(from_attributes=True)


class AtendimentoCreate(BaseModel):
    # UUID gerado no celular: se o app reenviar, o servidor reconhece (usado na sincronização)
    id: uuid.UUID | None = None

    # Informe o paciente NOVO (paciente) OU um já cadastrado (paciente_id)
    paciente: PacienteIn | None = None
    paciente_id: uuid.UUID | None = None

    data_hora: datetime
    relato_texto: str | None = None
    sintomas: list[str] = Field(default_factory=list, max_length=30)
    dias_sintomas: int | None = Field(default=None, ge=0, le=30)  # há quantos dias começaram os sintomas
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

    @field_validator("sintomas")
    @classmethod
    def padronizar_sintomas(cls, v: list[str]) -> list[str]:
        return normalizar_sintomas(v)

    @model_validator(mode="after")
    def paciente_ou_id(self):
        if (self.paciente is None) == (self.paciente_id is None):
            raise ValueError("Informe o paciente (novo) OU o paciente_id (já cadastrado), mas não os dois.")
        return self


class AtendimentoOut(BaseModel):
    id: uuid.UUID
    agente_id: uuid.UUID
    medico_id: uuid.UUID | None = None
    paciente: PacienteOut
    data_hora: datetime
    relato_texto: str | None = None
    sintomas: list[str] = Field(default_factory=list)
    dias_sintomas: int | None = None
    relato_voz_path: str | None = None
    imagem_exantema_path: str | None = None
    latitude: float
    longitude: float
    rua: str | None = None
    bairro: str | None = None
    municipio: str | None = None
    probabilidade_dengue: float | None = None
    nivel_risco: NivelRisco | None = None
    status_sincronizacao: StatusSincronizacao
    status_validacao: StatusValidacao
    parecer_medico: str | None = None
    encaminhamento: str | None = None
    atualizado_em: datetime
    resultado: ResultadoInferenciaOut | None = None
