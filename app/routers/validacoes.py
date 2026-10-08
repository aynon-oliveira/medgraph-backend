import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import case, select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.deps import require_perfil
from app.models import Atendimento, NivelRisco, Perfil, StatusValidacao, Usuario
from app.routers.atendimentos import _carregar, _para_saida
from app.schemas.atendimento import AtendimentoOut
from app.schemas.validacao import ValidacaoIn
from app.services import auditoria_service

router = APIRouter(tags=["Validação médica"])


@router.get("/validacoes/fila", response_model=list[AtendimentoOut])
def fila_de_validacao(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    medico: Usuario = Depends(require_perfil(Perfil.MEDICO)),
):
    """Atendimentos aguardando parecer: ALTO risco primeiro, depois os mais antigos."""
    prioridade = case((Atendimento.nivel_risco == NivelRisco.ALTO, 0), else_=1)
    consulta = (
        select(Atendimento)
        .options(selectinload(Atendimento.paciente), selectinload(Atendimento.resultado))
        .where(Atendimento.status_validacao == StatusValidacao.PENDENTE)
        .order_by(prioridade, Atendimento.data_hora.asc(), Atendimento.id)  # id desempata: paginação estável
        .limit(limit)
        .offset(offset)
    )
    saida = [_para_saida(a) for a in db.scalars(consulta).all()]
    auditoria_service.registrar_acesso(
        db, medico, auditoria_service.VER_FILA_VALIDACAO, "atendimento", request=request, detalhe=f"{len(saida)} registros"
    )
    return saida


@router.post("/atendimentos/{atendimento_id}/validacao", response_model=AtendimentoOut)
def validar_atendimento(
    request: Request,
    atendimento_id: uuid.UUID,
    dados: ValidacaoIn,
    db: Session = Depends(get_db),
    medico: Usuario = Depends(require_perfil(Perfil.MEDICO)),
):
    """O médico registra o parecer e o encaminhamento definitivos (RF08).

    - Só o perfil MEDICO pode validar.
    - Depois de validado, só o mesmo médico pode alterar o parecer (outro recebe 409).
    - O parecer validado prevalece sobre o que o celular enviar depois (RN06).
    """
    atendimento = _carregar(db, atendimento_id)
    if atendimento is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Atendimento não encontrado.")

    if (
        atendimento.status_validacao != StatusValidacao.PENDENTE
        and atendimento.medico_id is not None
        and atendimento.medico_id != medico.id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este atendimento já foi avaliado por outro médico.",
        )

    atendimento.status_validacao = dados.decisao
    atendimento.parecer_medico = dados.parecer
    atendimento.encaminhamento = dados.encaminhamento
    atendimento.medico_id = medico.id
    if dados.nivel_risco is not None:
        atendimento.nivel_risco = dados.nivel_risco
    atendimento.atualizado_em = datetime.now(timezone.utc)
    db.commit()

    saida = _para_saida(_carregar(db, atendimento_id))
    auditoria_service.registrar_acesso(
        db, medico, auditoria_service.VALIDAR_ATENDIMENTO, "atendimento", atendimento_id, request, detalhe=dados.decisao.value
    )
    return saida
