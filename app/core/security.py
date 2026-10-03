from datetime import datetime, timedelta, timezone

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings
from app.models.enums import Perfil

ALGORITMO = "HS256"
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_senha(senha: str) -> str:
    """Transforma a senha em um hash (a senha em texto puro nunca é salva)."""
    return _pwd.hash(senha)


def verificar_senha(senha: str, senha_hash: str) -> bool:
    return _pwd.verify(senha, senha_hash)


def criar_token(usuario_id, perfil: Perfil) -> str:
    """Gera o JWT. Guarda o id (sub), o perfil e a data de expiração."""
    expira = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    dados = {"sub": str(usuario_id), "perfil": perfil.value, "exp": expira}
    return jwt.encode(dados, settings.SECRET_KEY, algorithm=ALGORITMO)


def decodificar_token(token: str) -> dict:
    """Valida a assinatura e a expiração. Lança JWTError se o token for inválido."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITMO])
