import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.deps import get_current_user, require_perfil
from app.models import (
    Atendimento,
    NivelRisco,
    Paciente,
    Perfil,
    ResultadoInferencia,
    StatusValidacao,
    Usuario,
)
from app.services.neo4j_service import projetar_em_segundo_plano
from app.schemas.atendimento import (
    AtendimentoCreate,
    AtendimentoOut,
    PacienteOut,
    ResultadoInferenciaOut,
)

router = APIRouter(prefix="/atendimentos", tags=["Atendimentos"])


def _para_saida(a: Atendimento) -> AtendimentoOut:
    """Converte o registro do banco na resposta da API (extrai latitude/longitude do ponto)."""
    ponto = to_shape(a.localizacao)
    return AtendimentoOut(
        id=a.id,
        agente_id=a.agente_id,
        medico_id=a.medico_id,
        paciente=PacienteOut.model_validate(a.paciente),
        data_hora=a.data_hora,
        relato_texto=a.relato_texto,
        sintomas=list(a.sintomas or []),
        relato_voz_path=a.relato_voz_path,
        imagem_exantema_path=a.imagem_exantema_path,
        latitude=ponto.y,
        longitude=ponto.x,
        rua=a.rua,
        bairro=a.bairro,
        municipio=a.municipio,
        probabilidade_dengue=a.probabilidade_dengue,
        nivel_risco=a.nivel_risco,
        status_sincronizacao=a.status_sincronizacao,
        status_validacao=a.status_validacao,
        parecer_medico=a.parecer_medico,
        encaminhamento=a.encaminhamento,
        atualizado_em=a.atualizado_em,
        resultado=ResultadoInferenciaOut.model_validate(a.resultado) if a.resultado else None,
    )


def _carregar(db: Session, atendimento_id: uuid.UUID) -> Atendimento | None:
    return db.scalar(
        select(Atendimento)
        .options(selectinload(Atendimento.paciente), selectinload(Atendimento.resultado))
        .where(Atendimento.id == atendimento_id)
    )


@router.post("", response_model=AtendimentoOut, status_code=status.HTTP_201_CREATED)
def criar_atendimento(
    dados: AtendimentoCreate,
    segundo_plano: BackgroundTasks,
    db: Session = Depends(get_db),
    agente: Usuario = Depends(require_perfil(Perfil.ACS)),  # só o ACS registra atendimentos
):
    """Registra um atendimento em campo (RF02). Latitude e longitude são obrigatórias (RN05)."""
    if dados.id is not None and db.get(Atendimento, dados.id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Já existe um atendimento com este id.")

    # Paciente: cria um novo ou reaproveita um já cadastrado
    if dados.paciente_id is not None:
        paciente = db.get(Paciente, dados.paciente_id)
        if paciente is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paciente não encontrado.")
    else:
        paciente = Paciente(**dados.paciente.model_dump())
        db.add(paciente)
        db.flush()  # gera o id do paciente

    # RN02: qualquer sinal de alarme classifica como ALTO risco / encaminhamento prioritário
    nivel = dados.nivel_risco
    if dados.resultado and dados.resultado.sinais_alarme:
        nivel = NivelRisco.ALTO

    atendimento = Atendimento(
        agente_id=agente.id,
        paciente_id=paciente.id,
        data_hora=dados.data_hora,
        relato_texto=dados.relato_texto,
        sintomas=dados.sintomas,
        relato_voz_path=dados.relato_voz_path,
        imagem_exantema_path=dados.imagem_exantema_path,
        localizacao=from_shape(Point(dados.longitude, dados.latitude), srid=4326),
        rua=dados.rua,
        bairro=dados.bairro,
        municipio=dados.municipio,
        probabilidade_dengue=dados.resultado.score_probabilidade if dados.resultado else None,
        nivel_risco=nivel,
    )
    if dados.id is not None:
        atendimento.id = dados.id
    db.add(atendimento)
    db.flush()

    if dados.resultado:
        db.add(
            ResultadoInferencia(
                atendimento_id=atendimento.id,
                score_probabilidade=dados.resultado.score_probabilidade,
                sinais_alarme=dados.resultado.sinais_alarme,
                recomendacao=dados.resultado.recomendacao,
            )
        )

    db.commit()
    segundo_plano.add_task(projetar_em_segundo_plano, [atendimento.id])  # RF09: atualiza o grafo
    return _para_saida(_carregar(db, atendimento.id))


@router.get("", response_model=list[AtendimentoOut])
def listar_atendimentos(
    nivel_risco: NivelRisco | None = None,
    status_validacao: StatusValidacao | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Lista atendimentos. O ACS vê só os seus; Médico e Gestor veem todos."""
    consulta = select(Atendimento).options(
        selectinload(Atendimento.paciente), selectinload(Atendimento.resultado)
    )
    if usuario.perfil == Perfil.ACS:
        consulta = consulta.where(Atendimento.agente_id == usuario.id)
    if nivel_risco is not None:
        consulta = consulta.where(Atendimento.nivel_risco == nivel_risco)
    if status_validacao is not None:
        consulta = consulta.where(Atendimento.status_validacao == status_validacao)

    consulta = consulta.order_by(Atendimento.data_hora.desc()).limit(limit).offset(offset)
    return [_para_saida(a) for a in db.scalars(consulta).all()]


@router.get("/{atendimento_id}", response_model=AtendimentoOut)
def obter_atendimento(
    atendimento_id: uuid.UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    atendimento = _carregar(db, atendimento_id)
    # Para o ACS, atendimento de outro agente aparece como "não encontrado"
    if atendimento is None or (usuario.perfil == Perfil.ACS and atendimento.agente_id != usuario.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Atendimento não encontrado.")
    return _para_saida(atendimento)
