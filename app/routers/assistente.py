"""Assistente de IA (Gemini): resumo da ficha, descrição da foto e resumo do painel. Sempre apoio, nunca decisão.

- Médico: resumo da ficha e descrição da foto (a foto exige `ciente=true`).
- Gestor: resumo do painel, feito só com números agregados (nenhum dado de paciente sai do servidor).
Nada é gravado no banco: o texto volta para a tela como RASCUNHO. O nível de risco nunca é alterado por aqui.
"""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_perfil
from app.models import Atendimento, Paciente, Perfil, Usuario
from app.routers.midia import _atendimento_visivel
from app.services import auditoria_service, gemini_service, midia_service
from app.services.imagem_limpa import remover_metadados

router = APIRouter(tags=["Assistente de IA (Gemini)"])

AVISO = "Texto gerado por IA como rascunho de apoio. Não é diagnóstico: confira com a ficha e decida pelo seu julgamento clínico."


class PedidoFoto(BaseModel):
    ciente: bool = Field(default=False, description="O médico confirma que a foto pode ser enviada ao Gemini (Google).")


class PedidoPainel(BaseModel):
    dias: int = Field(default=30, ge=1, le=365)


def _chamar(usuario: Usuario, funcao):
    """Aplica o limite por usuário e traduz os erros do Gemini em respostas HTTP claras."""
    try:
        gemini_service.conferir_limite(usuario.id)
        return funcao()
    except gemini_service.IALimite:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Limite de pedidos ao assistente atingido. Tente mais tarde.")
    except gemini_service.IAIndisponivel:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="O assistente de IA não está disponível agora. O atendimento segue normalmente.")
    except gemini_service.IAFalhou:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="O assistente não conseguiu responder. Tente de novo.")


def _exigir_ligado():
    if not gemini_service.disponivel():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="O assistente de IA não está ativado neste servidor.")


def _idade(paciente: Paciente | None, em: date) -> int | None:
    if paciente is None or paciente.data_nascimento is None:
        return None
    n = paciente.data_nascimento
    return em.year - n.year - ((em.month, em.day) < (n.month, n.day))


@router.get("/api/v1/assistente/status")
def status_assistente(_: Usuario = Depends(get_current_user)):
    """O site consulta isto para mostrar ou esconder os botões do assistente."""
    return {"disponivel": gemini_service.disponivel(), "modelo": gemini_service.modelo() if gemini_service.disponivel() else None}


@router.post("/atendimentos/{atendimento_id}/assistente/resumo")
def resumir_ficha(
    request: Request,
    atendimento_id: uuid.UUID,
    db: Session = Depends(get_db),
    medico: Usuario = Depends(require_perfil(Perfil.MEDICO)),
):
    a: Atendimento = _atendimento_visivel(db, atendimento_id, medico)
    _exigir_ligado()
    transcricao = db.execute(text("SELECT transcricao_audio FROM atendimentos WHERE id = :i"), {"i": str(a.id)}).scalar()
    r = a.resultado
    entrada = gemini_service.preparar_ficha(
        idade=_idade(a.paciente, a.data_hora.date()),
        sexo=getattr(a.paciente, "sexo", None),
        sintomas=a.sintomas,
        relato=a.relato_texto,
        transcricao=transcricao,
        nivel=a.nivel_risco.value if a.nivel_risco else None,
        score=r.score_probabilidade if r else None,
        sinais_alarme=r.sinais_alarme if r else [],
        recomendacao=r.recomendacao if r else None,
    )
    texto = _chamar(medico, lambda: gemini_service.gerar(gemini_service.INSTRUCAO_FICHA, entrada))
    auditoria_service.registrar_acesso(db, medico, auditoria_service.USAR_ASSISTENTE_IA, "atendimento", atendimento_id, request, detalhe="resumo")
    return {
        "atendimento_id": atendimento_id,
        "resumo": texto,
        "nivel_risco": a.nivel_risco.value if a.nivel_risco else None,  # inalterado
        "modelo": gemini_service.modelo(),
        "aviso": AVISO,
    }


@router.post("/atendimentos/{atendimento_id}/assistente/foto")
def descrever_foto(
    request: Request,
    atendimento_id: uuid.UUID,
    dados: PedidoFoto,
    db: Session = Depends(get_db),
    medico: Usuario = Depends(require_perfil(Perfil.MEDICO)),
):
    a = _atendimento_visivel(db, atendimento_id, medico)
    if not dados.ciente:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Confirme que a foto pode ser enviada ao Gemini (Google).")
    caminho = midia_service.resolver(a.imagem_exantema_path)
    if caminho is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Este atendimento não tem foto.")
    _exigir_ligado()
    imagem = remover_metadados(caminho.read_bytes())  # passo 28: fotos antigas também saem sem GPS
    mime = midia_service.tipo_do_arquivo(caminho)
    texto = _chamar(medico, lambda: gemini_service.gerar(
        gemini_service.INSTRUCAO_FOTO,
        "<dados>Foto de pele anexa. Descreva apenas o que é visível.</dados>", imagem=imagem, mime=mime))
    auditoria_service.registrar_acesso(db, medico, auditoria_service.USAR_ASSISTENTE_IA, "atendimento", atendimento_id, request, detalhe="foto")
    return {"atendimento_id": atendimento_id, "descricao": texto, "modelo": gemini_service.modelo(), "aviso": AVISO}


def agregar_painel(db: Session, dias: int) -> dict:
    """Números do período e do período anterior (de mesma duração). Sem nenhum dado de paciente."""
    p = {"d": dias}
    atual = db.execute(text(
        "SELECT count(*) AS total, count(*) FILTER (WHERE nivel_risco::text='ALTO') AS alto, "
        "count(*) FILTER (WHERE nivel_risco::text='MODERADO') AS moderado "
        "FROM atendimentos WHERE data_hora >= now() - make_interval(days => :d)"), p).one()
    anterior = db.execute(text(
        "SELECT count(*) FROM atendimentos WHERE data_hora >= now() - make_interval(days => 2 * :d) "
        "AND data_hora < now() - make_interval(days => :d)"), p).scalar() or 0
    locais = db.execute(text(
        "SELECT municipio, bairro, count(*) AS total, count(*) FILTER (WHERE nivel_risco::text='ALTO') AS alto "
        "FROM atendimentos WHERE municipio IS NOT NULL AND data_hora >= now() - make_interval(days => :d) "
        "GROUP BY municipio, bairro ORDER BY total DESC LIMIT 8"), p).all()
    sintomas = db.execute(text(
        "SELECT s AS nome, count(*) AS total FROM atendimentos, unnest(sintomas) AS s "
        "WHERE data_hora >= now() - make_interval(days => :d) GROUP BY s ORDER BY total DESC LIMIT 8"), p).all()
    return {
        "dias": dias, "total": atual.total, "alto": atual.alto, "moderado": atual.moderado, "periodo_anterior": anterior,
        "localidades": [{"nome": f"{l.bairro + ', ' if l.bairro else ''}{l.municipio}", "total": l.total, "alto": l.alto} for l in locais],
        "sintomas": [{"nome": s.nome, "total": s.total} for s in sintomas],
    }


def texto_painel(ag: dict) -> str:
    linhas = [
        f"Período: últimos {ag['dias']} dias",
        f"Atendimentos no período: {ag['total']} (alto risco: {ag['alto']}, moderado: {ag['moderado']})",
        f"Atendimentos nos {ag['dias']} dias anteriores: {ag['periodo_anterior']}",
        "Localidades com mais atendimentos: " + ("; ".join(f"{l['nome']} ({l['total']}, {l['alto']} alto risco)" for l in ag["localidades"]) or "nenhuma"),
        "Sintomas mais frequentes: " + ("; ".join(f"{s['nome']} ({s['total']})" for s in ag["sintomas"]) or "nenhum"),
    ]
    return "<dados>\n" + "\n".join(linhas) + "\n</dados>"


@router.post("/api/v1/assistente/painel")
def resumir_painel(
    request: Request,
    dados: PedidoPainel,
    db: Session = Depends(get_db),
    gestor: Usuario = Depends(require_perfil(Perfil.GESTOR)),
):
    _exigir_ligado()
    ag = agregar_painel(db, dados.dias)
    if ag["total"] == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Não há atendimentos nesse período para resumir.")
    texto = _chamar(gestor, lambda: gemini_service.gerar(gemini_service.INSTRUCAO_PAINEL, texto_painel(ag)))
    auditoria_service.registrar_acesso(db, gestor, auditoria_service.USAR_ASSISTENTE_IA, "painel", None, request, detalhe=f"{dados.dias}d")
    return {"resumo": texto, "numeros": ag, "modelo": gemini_service.modelo(), "aviso": AVISO}
