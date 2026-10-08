import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_perfil
from app.models import Atendimento, Perfil, Usuario
from app.services import auditoria_service, midia_service

router = APIRouter(prefix="/atendimentos", tags=["Mídia (áudio e foto)"])

Categoria = Literal["audio", "foto"]
CAMPO = {"audio": "relato_voz_path", "foto": "imagem_exantema_path"}


def _atendimento_visivel(db: Session, atendimento_id: uuid.UUID, usuario: Usuario) -> Atendimento:
    """ACS só enxerga os próprios atendimentos; para os outros perfis, 'não encontrado'."""
    a = db.get(Atendimento, atendimento_id)
    if a is None or (usuario.perfil == Perfil.ACS and a.agente_id != usuario.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Atendimento não encontrado.")
    return a


@router.put("/{atendimento_id}/midia/{categoria}", status_code=status.HTTP_200_OK)
def enviar_midia(
    atendimento_id: uuid.UUID,
    categoria: Categoria,
    arquivo: UploadFile = File(...),
    db: Session = Depends(get_db),
    agente: Usuario = Depends(require_perfil(Perfil.ACS)),
):
    """Anexa (ou substitui) o áudio do relato ou a foto do exantema. Só o ACS dono do atendimento.

    PUT é idempotente: se o celular reenviar o mesmo arquivo depois de uma queda de conexão,
    o resultado é o mesmo e o arquivo antigo é apagado.
    """
    atendimento = _atendimento_visivel(db, atendimento_id, agente)
    novo = midia_service.salvar(categoria, atendimento.id, arquivo)
    antigo = getattr(atendimento, CAMPO[categoria])
    setattr(atendimento, CAMPO[categoria], novo)
    try:
        db.commit()
    except Exception:
        db.rollback()
        midia_service.remover(novo)
        raise
    midia_service.remover(antigo)
    return {"atendimento_id": atendimento.id, "categoria": categoria, "caminho": novo}


@router.get("/{atendimento_id}/midia/{categoria}")
def baixar_midia(
    request: Request,
    atendimento_id: uuid.UUID,
    categoria: Categoria,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_perfil(Perfil.ACS, Perfil.MEDICO)),
):
    """Baixa o arquivo. O Gestor não acessa (LGPD): ele trabalha com dados agregados, sem identificar o paciente."""
    atendimento = _atendimento_visivel(db, atendimento_id, usuario)
    caminho = midia_service.resolver(getattr(atendimento, CAMPO[categoria]))
    if caminho is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Este atendimento não tem esse arquivo.")
    auditoria_service.registrar_acesso(
        db, usuario, auditoria_service.BAIXAR_MIDIA, "atendimento", atendimento_id, request, detalhe=categoria
    )
    return FileResponse(
        caminho,
        media_type=midia_service.tipo_do_arquivo(caminho),
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
