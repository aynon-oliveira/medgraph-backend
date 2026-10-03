from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_perfil
from app.models import Perfil, Usuario
from app.schemas.usuario import UsuarioOut

router = APIRouter(prefix="/usuarios", tags=["Usuários"])


@router.get("", response_model=list[UsuarioOut])
def listar_usuarios(
    db: Session = Depends(get_db),
    _: Usuario = Depends(require_perfil(Perfil.GESTOR)),  # só o GESTOR pode listar
):
    return db.scalars(select(Usuario).order_by(Usuario.nome)).all()
