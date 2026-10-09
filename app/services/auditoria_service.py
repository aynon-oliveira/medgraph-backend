"""Registro de acessos a dados de saúde (LGPD).

Regras:
- Só registra quem NÃO é o ACS dono do dado: o ACS só enxerga os próprios atendimentos (já limitado em cada rota).
  Médico e Gestor são os acessos de terceiros que precisam de rastro.
- Grava referência (tipo + id), nunca o conteúdo consultado.
- Se o registro falhar, a consulta NÃO é derrubada (o agente em campo não pode ficar sem atendimento por um
  problema de auditoria), mas o erro vai para o log do servidor para ser corrigido.
"""
import logging
import uuid

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import Perfil, RegistroAcesso, Usuario

logger = logging.getLogger("medgraph.auditoria")

LER_ATENDIMENTO = "LER_ATENDIMENTO"
LISTAR_ATENDIMENTOS = "LISTAR_ATENDIMENTOS"
BAIXAR_MIDIA = "BAIXAR_MIDIA"
VER_FILA_VALIDACAO = "VER_FILA_VALIDACAO"
VALIDAR_ATENDIMENTO = "VALIDAR_ATENDIMENTO"
VER_AUDITORIA = "VER_AUDITORIA"
USAR_ASSISTENTE_IA = "USAR_ASSISTENTE_IA"
TRANSCREVER_AUDIO = "TRANSCREVER_AUDIO"

ACOES = (USAR_ASSISTENTE_IA, TRANSCREVER_AUDIO, LER_ATENDIMENTO, LISTAR_ATENDIMENTOS, BAIXAR_MIDIA, VER_FILA_VALIDACAO, VALIDAR_ATENDIMENTO, VER_AUDITORIA)


def _ip(request: Request | None) -> str | None:
    # Atrás de proxy/túnel este é o IP do proxy. Não confiamos em X-Forwarded-For (pode ser forjado pelo cliente).
    if request is None or request.client is None:
        return None
    return request.client.host[:45]


def registrar_acesso(
    db: Session,
    usuario: Usuario,
    acao: str,
    recurso: str,
    recurso_id: uuid.UUID | None = None,
    request: Request | None = None,
    detalhe: str | None = None,
) -> None:
    if usuario.perfil == Perfil.ACS:
        return
    try:
        db.add(
            RegistroAcesso(
                usuario_id=usuario.id,
                perfil=usuario.perfil.value,
                acao=acao,
                recurso=recurso,
                recurso_id=recurso_id,
                detalhe=detalhe[:200] if detalhe else None,
                ip=_ip(request),
            )
        )
        db.commit()  # chame DEPOIS de montar a resposta: o commit expira os objetos carregados
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("Falha ao registrar o acesso (%s) — a consulta seguiu normalmente.", acao)
