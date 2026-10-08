import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decodificar_token, token_vale_apos_troca_de_senha
from app.models import Perfil, Usuario

# tokenUrl faz o botão "Authorize" do /docs funcionar
oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")
oauth2_opcional = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

_NAO_AUTENTICADO = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Credenciais inválidas ou expiradas.",
    headers={"WWW-Authenticate": "Bearer"},
)


def _usuario_do_token(token: str, db: Session) -> Usuario:
    try:
        payload = decodificar_token(token)
        usuario_id = uuid.UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise _NAO_AUTENTICADO
    usuario = db.get(Usuario, usuario_id)
    # Usuário desativado perde o acesso NA HORA (mesmo com token ainda dentro do prazo);
    # e trocar a senha derruba as sessões abertas antes da troca.
    if usuario is None or not usuario.ativo or not token_vale_apos_troca_de_senha(payload, usuario.senha_alterada_em):
        raise _NAO_AUTENTICADO
    return usuario


def get_current_user(token: str = Depends(oauth2), db: Session = Depends(get_db)) -> Usuario:
    """Exige login. Devolve o usuário dono do token."""
    return _usuario_do_token(token, db)


def get_current_user_optional(
    token: str | None = Depends(oauth2_opcional), db: Session = Depends(get_db)
) -> Usuario | None:
    """Login opcional (usado só no cadastro do primeiro usuário)."""
    if not token:
        return None
    return _usuario_do_token(token, db)


def require_perfil(*perfis: Perfil):
    """Uso: Depends(require_perfil(Perfil.GESTOR)) — só esses perfis passam."""

    def verificador(usuario: Usuario = Depends(get_current_user)) -> Usuario:
        if usuario.perfil not in perfis:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Seu perfil não tem permissão para esta ação.",
            )
        return usuario

    return verificador
