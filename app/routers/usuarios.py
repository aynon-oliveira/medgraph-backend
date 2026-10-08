import secrets
import string
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_perfil
from app.core.security import hash_senha
from app.models import Perfil, Usuario
from app.schemas.usuario import SenhaTemporariaOut, UsuarioOut, UsuarioUpdate

router = APIRouter(prefix="/usuarios", tags=["Usuários"])

_ALFABETO = string.ascii_letters + string.digits


def _gerar_senha_temporaria() -> str:
    """12 caracteres aleatórios, sempre com letra e número."""
    while True:
        senha = "".join(secrets.choice(_ALFABETO) for _ in range(12))
        if any(c.isalpha() for c in senha) and any(c.isdigit() for c in senha):
            return senha


def _buscar(db: Session, usuario_id: uuid.UUID) -> Usuario:
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")
    return usuario


@router.get("", response_model=list[UsuarioOut])
def listar_usuarios(
    perfil: Perfil | None = None,
    ativo: bool | None = None,
    busca: str | None = Query(default=None, max_length=100, description="Parte do nome ou do e-mail"),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: Usuario = Depends(require_perfil(Perfil.GESTOR)),  # só o GESTOR pode listar
):
    consulta = select(Usuario)
    if perfil is not None:
        consulta = consulta.where(Usuario.perfil == perfil)
    if ativo is not None:
        consulta = consulta.where(Usuario.ativo == ativo)
    if busca:
        termo = f"%{busca.strip()}%"
        consulta = consulta.where(or_(Usuario.nome.ilike(termo), Usuario.email.ilike(termo)))
    return db.scalars(consulta.order_by(Usuario.nome, Usuario.id).limit(limit).offset(offset)).all()


@router.get("/{usuario_id}", response_model=UsuarioOut)
def obter_usuario(
    usuario_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Usuario = Depends(require_perfil(Perfil.GESTOR)),
):
    return _buscar(db, usuario_id)


@router.patch("/{usuario_id}", response_model=UsuarioOut)
def corrigir_usuario(
    usuario_id: uuid.UUID,
    dados: UsuarioUpdate,
    db: Session = Depends(get_db),
    _: Usuario = Depends(require_perfil(Perfil.GESTOR)),
):
    """Corrige nome, microárea, CRM ou departamento. Só os campos enviados mudam."""
    usuario = _buscar(db, usuario_id)
    mudancas = dados.model_dump(exclude_unset=True)
    if not mudancas:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Envie ao menos um campo para alterar.")
    if "nome" in mudancas and mudancas["nome"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="O nome não pode ficar vazio.")
    for campo, valor in mudancas.items():
        setattr(usuario, campo, valor)
    db.commit()
    db.refresh(usuario)
    return usuario


@router.post("/{usuario_id}/desativar", response_model=UsuarioOut)
def desativar_usuario(
    usuario_id: uuid.UUID,
    db: Session = Depends(get_db),
    gestor: Usuario = Depends(require_perfil(Perfil.GESTOR)),
):
    """Desativa (não apaga): o histórico de atendimentos e pareceres é mantido, mas o login e os tokens
    já emitidos deixam de funcionar NA HORA. Um GESTOR não pode desativar a si mesmo; como quem desativa
    é sempre um GESTOR ativo diferente do alvo, o sistema nunca fica sem gestor."""
    if usuario_id == gestor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Você não pode desativar a sua própria conta.")
    usuario = _buscar(db, usuario_id)
    usuario.ativo = False
    db.commit()
    db.refresh(usuario)
    return usuario


@router.post("/{usuario_id}/reativar", response_model=UsuarioOut)
def reativar_usuario(
    usuario_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Usuario = Depends(require_perfil(Perfil.GESTOR)),
):
    usuario = _buscar(db, usuario_id)
    usuario.ativo = True
    db.commit()
    db.refresh(usuario)
    return usuario


@router.post("/{usuario_id}/redefinir-senha", response_model=SenhaTemporariaOut)
def redefinir_senha(
    usuario_id: uuid.UUID,
    db: Session = Depends(get_db),
    gestor: Usuario = Depends(require_perfil(Perfil.GESTOR)),
):
    """Para quem esqueceu a senha: gera uma senha temporária (mostrada uma única vez) e derruba as
    sessões abertas desse usuário. O gestor não vê nem escolhe a senha definitiva dele."""
    if usuario_id == gestor.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Para a sua própria conta use POST /auth/trocar-senha."
        )
    usuario = _buscar(db, usuario_id)
    temporaria = _gerar_senha_temporaria()
    usuario.senha_hash = hash_senha(temporaria)
    usuario.senha_alterada_em = datetime.now(timezone.utc)
    db.commit()
    db.refresh(usuario)
    return SenhaTemporariaOut(usuario=usuario, senha_temporaria=temporaria)
