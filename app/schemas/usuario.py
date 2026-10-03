import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import Perfil


class UsuarioCreate(BaseModel):
    nome: str = Field(min_length=3, max_length=150)
    email: str = Field(max_length=150)
    senha: str = Field(min_length=8, max_length=72)  # bcrypt aceita até 72 caracteres
    perfil: Perfil
    cpf: str | None = Field(default=None, max_length=14)
    microarea: str | None = Field(default=None, max_length=50)       # ACS
    crm: str | None = Field(default=None, max_length=30)             # Médico
    departamento: str | None = Field(default=None, max_length=100)   # Gestor

    @field_validator("email")
    @classmethod
    def email_valido(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or v.startswith("@") or v.endswith("@") or " " in v:
            raise ValueError("E-mail inválido.")
        return v


class UsuarioOut(BaseModel):
    """O que a API devolve. Nunca inclui a senha nem o hash."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nome: str
    email: str
    perfil: Perfil
    microarea: str | None = None
    crm: str | None = None
    departamento: str | None = None
    ativo: bool


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    perfil: Perfil
