import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_perfil
from app.models import Perfil, Usuario
from app.routers.midia import _atendimento_visivel
from app.services import auditoria_service, midia_service, transcricao_service

router = APIRouter(prefix="/atendimentos", tags=["Transcrição do áudio (Whisper)"])


def _ler_salva(db: Session, atendimento_id: uuid.UUID) -> str | None:
    return db.execute(
        text("SELECT transcricao_audio FROM atendimentos WHERE id = :i"), {"i": str(atendimento_id)}
    ).scalar()


@router.post("/{atendimento_id}/transcricao")
def transcrever_audio(
    request: Request,
    atendimento_id: uuid.UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_perfil(Perfil.ACS, Perfil.MEDICO)),
):
    """Transcreve o áudio do relato (rascunho para conferência) e guarda em `transcricao_audio`.

    Não altera `relato_texto`, os sintomas nem o risco. Demora alguns segundos (roda no servidor, só com CPU).
    """
    atendimento = _atendimento_visivel(db, atendimento_id, usuario)
    caminho = midia_service.resolver(atendimento.relato_voz_path)
    if caminho is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Este atendimento não tem áudio para transcrever.")
    if not transcricao_service.disponivel():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A transcrição não está disponível neste servidor. O relato pode ser digitado normalmente.",
        )
    try:
        r = transcricao_service.transcrever(caminho)
    except transcricao_service.TranscricaoIndisponivel:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="A transcrição não está disponível agora.")
    except transcricao_service.TranscricaoFalhou:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Não foi possível transcrever este áudio.")
    db.execute(
        text("UPDATE atendimentos SET transcricao_audio = :t WHERE id = :i"),
        {"t": r["texto"], "i": str(atendimento.id)},
    )
    db.commit()
    auditoria_service.registrar_acesso(
        db, usuario, auditoria_service.TRANSCREVER_AUDIO, "atendimento", atendimento_id, request, detalhe=r["modelo"]
    )
    return {"atendimento_id": atendimento_id, "transcricao": r["texto"], "idioma": r["idioma"], "modelo": r["modelo"]}


@router.get("/{atendimento_id}/transcricao")
def ler_transcricao(
    request: Request,
    atendimento_id: uuid.UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_perfil(Perfil.ACS, Perfil.MEDICO)),
):
    """Devolve a transcrição já guardada (sem transcrever de novo)."""
    _atendimento_visivel(db, atendimento_id, usuario)
    texto = _ler_salva(db, atendimento_id)
    if not texto:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Este atendimento ainda não foi transcrito.")
    auditoria_service.registrar_acesso(
        db, usuario, auditoria_service.LER_ATENDIMENTO, "atendimento", atendimento_id, request, detalhe="transcricao"
    )
    return {"atendimento_id": atendimento_id, "transcricao": texto}
