import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_perfil
from app.models import Perfil, RegistroAcesso, Usuario
from app.services import auditoria_service

router = APIRouter(prefix="/auditoria", tags=["Auditoria (LGPD)"])


class RegistroAcessoOut(BaseModel):
    id: uuid.UUID
    criado_em: datetime
    usuario_id: uuid.UUID
    usuario_nome: str | None  # None se o usuário não existir mais
    perfil: str
    acao: str
    recurso: str
    recurso_id: uuid.UUID | None
    detalhe: str | None
    ip: str | None


@router.get("/acessos", response_model=list[RegistroAcessoOut])
def listar_acessos(
    request: Request,
    usuario_id: uuid.UUID | None = None,
    acao: str | None = Query(default=None, max_length=40),
    recurso_id: uuid.UUID | None = Query(default=None, description="Id do atendimento consultado"),
    desde: datetime | None = None,
    ate: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    gestor: Usuario = Depends(require_perfil(Perfil.GESTOR)),
):
    """Quem consultou dados de saúde e quando (LGPD). Só o Gestor; somente leitura — nada aqui apaga ou altera registros.

    Responde, por exemplo, "quem abriu o atendimento X?" (recurso_id) ou "o que o médico Y consultou?" (usuario_id).
    """
    consulta = select(RegistroAcesso, Usuario.nome).outerjoin(Usuario, Usuario.id == RegistroAcesso.usuario_id)
    if usuario_id is not None:
        consulta = consulta.where(RegistroAcesso.usuario_id == usuario_id)
    if acao is not None:
        consulta = consulta.where(RegistroAcesso.acao == acao)
    if recurso_id is not None:
        consulta = consulta.where(RegistroAcesso.recurso_id == recurso_id)
    if desde is not None:
        consulta = consulta.where(RegistroAcesso.criado_em >= desde)
    if ate is not None:
        consulta = consulta.where(RegistroAcesso.criado_em <= ate)
    consulta = consulta.order_by(RegistroAcesso.criado_em.desc(), RegistroAcesso.id).limit(limit).offset(offset)

    linhas = [
        RegistroAcessoOut(
            id=r.id, criado_em=r.criado_em, usuario_id=r.usuario_id, usuario_nome=nome, perfil=r.perfil,
            acao=r.acao, recurso=r.recurso, recurso_id=r.recurso_id, detalhe=r.detalhe, ip=r.ip,
        )
        for r, nome in db.execute(consulta).all()
    ]
    auditoria_service.registrar_acesso(db, gestor, auditoria_service.VER_AUDITORIA, "auditoria", request=request)
    return linhas
