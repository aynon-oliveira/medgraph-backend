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


# ---------------------------------------------------------------- passo 15: gestão de usuários
SENHAS_COMUNS = {
    "12345678", "123456789", "1234567890", "password", "password1", "senha123", "senha1234", "senha12345",
    "qwerty123", "admin123", "abc12345", "medgraph", "medgraph123", "mudar123", "trocar123",
}


def problemas_da_senha(senha: str, email: str = "", nome: str = "") -> list[str]:
    """Regras para senhas NOVAS (troca e redefinição). Devolve a lista do que está errado."""
    problemas = []
    if len(senha) < 8:
        problemas.append("A senha precisa ter pelo menos 8 caracteres.")
    if len(senha.encode("utf-8")) > 72:
        problemas.append("A senha pode ter no máximo 72 bytes.")
    if not any(c.isalpha() for c in senha) or not any(c.isdigit() for c in senha):
        problemas.append("A senha precisa ter letras e números.")
    baixa = senha.lower()
    if baixa in SENHAS_COMUNS:
        problemas.append("Essa senha é muito comum. Escolha outra.")
    parte_email = email.split("@")[0].lower()
    if len(parte_email) >= 3 and parte_email in baixa:
        problemas.append("A senha não pode conter o seu e-mail.")
    for palavra in nome.lower().split():
        if len(palavra) >= 4 and palavra in baixa:
            problemas.append("A senha não pode conter o seu nome.")
            break
    return problemas


class TrocarSenhaIn(BaseModel):
    senha_atual: str = Field(min_length=1, max_length=72)
    senha_nova: str = Field(min_length=8, max_length=72)


class UsuarioUpdate(BaseModel):
    """Campos que o GESTOR pode corrigir. E-mail e perfil não mudam aqui (evita troca indevida de poder)."""

    nome: str | None = Field(default=None, min_length=3, max_length=150)
    microarea: str | None = Field(default=None, max_length=50)
    crm: str | None = Field(default=None, max_length=30)
    departamento: str | None = Field(default=None, max_length=100)

    model_config = ConfigDict(extra="forbid")


class MensagemOut(BaseModel):
    detail: str


class SenhaTemporariaOut(BaseModel):
    usuario: UsuarioOut
    senha_temporaria: str
    aviso: str = "Mostrada só desta vez. Entregue ao usuário por um canal seguro e peça que troque em /auth/trocar-senha."
