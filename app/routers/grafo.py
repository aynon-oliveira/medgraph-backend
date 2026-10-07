import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_perfil
from app.models import Perfil, Usuario
from app.schemas.grafo import (
    FocoIn,
    FocoOut,
    LocalidadeGrafo,
    ReconstrucaoOut,
    ResumoGrafo,
    SintomaFrequencia,
)
from app.services import neo4j_service as grafo

logger = logging.getLogger("medgraph.grafo")

router = APIRouter(prefix="/grafo", tags=["Grafo epidemiológico"])

_INDISPONIVEL = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="O grafo (Neo4j) está indisponível no momento. Tente novamente em instantes.",
)

LEITORES = (Perfil.MEDICO, Perfil.GESTOR)


@router.get("/resumo", response_model=ResumoGrafo)
def resumo_do_grafo(_: Usuario = Depends(require_perfil(*LEITORES))):
    """Quantos nós e relações existem no grafo, por tipo."""
    try:
        return grafo.resumo()
    except Exception:  # noqa: BLE001
        logger.warning("Falha ao consultar o grafo.", exc_info=True)
        raise _INDISPONIVEL


@router.get("/sintomas/frequencia", response_model=list[SintomaFrequencia])
def frequencia_de_sintomas(
    limit: int = Query(20, ge=1, le=200),
    _: Usuario = Depends(require_perfil(*LEITORES)),
):
    """Sintomas mais frequentes (por número de pacientes distintos)."""
    try:
        return grafo.frequencia_sintomas(limit)
    except Exception:  # noqa: BLE001
        logger.warning("Falha ao consultar o grafo.", exc_info=True)
        raise _INDISPONIVEL


@router.get("/localidades", response_model=list[LocalidadeGrafo])
def localidades_do_grafo(
    limit: int = Query(50, ge=1, le=200),
    _: Usuario = Depends(require_perfil(*LEITORES)),
):
    """Localidades com o número de pacientes e de focos do vetor."""
    try:
        return grafo.localidades(limit)
    except Exception:  # noqa: BLE001
        logger.warning("Falha ao consultar o grafo.", exc_info=True)
        raise _INDISPONIVEL


@router.post("/focos", response_model=FocoOut, status_code=status.HTTP_201_CREATED)
def registrar_foco(dados: FocoIn, _: Usuario = Depends(require_perfil(Perfil.GESTOR))):
    """O gestor registra um foco do vetor e ele é ligado à localidade (LOCALIZADO_EM)."""
    try:
        return grafo.criar_foco(
            dados.tipo_criadouro,
            dados.densidade_vetorial,
            dados.data_identificacao,
            dados.municipio,
            dados.bairro,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Falha ao gravar o foco no grafo.", exc_info=True)
        raise _INDISPONIVEL


@router.post("/reconstruir", response_model=ReconstrucaoOut)
def reconstruir(db: Session = Depends(get_db), _: Usuario = Depends(require_perfil(Perfil.GESTOR))):
    """Refaz o grafo a partir dos atendimentos do PostgreSQL (seguro repetir: não duplica)."""
    try:
        return ReconstrucaoOut(atendimentos_projetados=grafo.reconstruir_grafo(db))
    except Exception:  # noqa: BLE001
        logger.warning("Falha ao reconstruir o grafo.", exc_info=True)
        raise _INDISPONIVEL
