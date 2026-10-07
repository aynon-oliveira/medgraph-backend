from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class FocoIn(BaseModel):
    """Foco do mosquito identificado pela vigilância (vira um NoFocoVetor)."""

    tipo_criadouro: str = Field(min_length=2, max_length=80)
    densidade_vetorial: Literal["BAIXA", "MEDIA", "ALTA"]
    data_identificacao: date
    municipio: str = Field(min_length=2, max_length=100)
    bairro: str | None = Field(default=None, max_length=100)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "tipo_criadouro": "Pneus a céu aberto",
                    "densidade_vetorial": "ALTA",
                    "data_identificacao": "2026-10-01",
                    "municipio": "Manaus",
                    "bairro": "Centro",
                }
            ]
        }
    }


class FocoOut(BaseModel):
    id: str
    localidade: str
    localidade_chave: str


class ResumoGrafo(BaseModel):
    nos: dict[str, int]
    relacoes: dict[str, int]


class SintomaFrequencia(BaseModel):
    sintoma: str
    sinal_alarme: bool | None = None
    pacientes: int


class LocalidadeGrafo(BaseModel):
    chave: str
    nome: str | None = None
    pacientes: int
    focos: int


class ReconstrucaoOut(BaseModel):
    atendimentos_projetados: int
