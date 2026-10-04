from pydantic import BaseModel, Field, field_validator

from app.models.enums import NivelRisco, StatusValidacao


class ValidacaoIn(BaseModel):
    """Decisão do médico sobre o indicativo da IA (RF08)."""

    decisao: StatusValidacao  # VALIDADO (concorda) ou REJEITADO (discorda)
    parecer: str = Field(min_length=5, max_length=2000)
    encaminhamento: str | None = Field(default=None, max_length=500)
    nivel_risco: NivelRisco | None = None  # opcional: o médico pode corrigir a classificação

    @field_validator("decisao")
    @classmethod
    def decisao_final(cls, v: StatusValidacao) -> StatusValidacao:
        if v == StatusValidacao.PENDENTE:
            raise ValueError("A decisão deve ser VALIDADO ou REJEITADO.")
        return v

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "decisao": "VALIDADO",
                    "parecer": "Quadro compatível com dengue com sinal de alarme. Confirmo a classificação.",
                    "encaminhamento": "Encaminhar à UPA mais próxima para hidratação e observação.",
                }
            ]
        }
    }
