import uuid

from fastapi import APIRouter, Depends
from geoalchemy2.shape import from_shape
from pydantic import ValidationError
from shapely.geometry import Point
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_perfil
from app.models import (
    Atendimento,
    NivelRisco,
    Paciente,
    Perfil,
    ResultadoInferencia,
    StatusSincronizacao,
    StatusValidacao,
    Usuario,
)
from app.schemas.sincronizacao import (
    AtendimentoSync,
    ItemSync,
    SincronizacaoIn,
    SincronizacaoOut,
    SituacaoSync,
)

router = APIRouter(prefix="/api/v1", tags=["Sincronização"])


def _nivel_final(dados: AtendimentoSync) -> NivelRisco | None:
    """RN02: qualquer sinal de alarme classifica o atendimento como ALTO risco."""
    if dados.resultado and dados.resultado.sinais_alarme:
        return NivelRisco.ALTO
    return dados.nivel_risco


def _id_ou_none(item: dict) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(item.get("id")))
    except ValueError:
        return None


def _gravar_resultado(db: Session, atendimento: Atendimento, dados: AtendimentoSync) -> None:
    if dados.resultado is None:
        return
    if atendimento.resultado is None:
        db.add(
            ResultadoInferencia(
                atendimento_id=atendimento.id,
                score_probabilidade=dados.resultado.score_probabilidade,
                sinais_alarme=dados.resultado.sinais_alarme,
                recomendacao=dados.resultado.recomendacao,
            )
        )
    else:
        atendimento.resultado.score_probabilidade = dados.resultado.score_probabilidade
        atendimento.resultado.sinais_alarme = dados.resultado.sinais_alarme
        atendimento.resultado.recomendacao = dados.resultado.recomendacao


def _processar(db: Session, usuario: Usuario, dados: AtendimentoSync) -> ItemSync:
    existente = db.get(Atendimento, dados.id)
    nivel = _nivel_final(dados)
    ponto = from_shape(Point(dados.longitude, dados.latitude), srid=4326)
    prob = dados.resultado.score_probabilidade if dados.resultado else None

    # --- Atendimento novo: inserir ---
    if existente is None:
        paciente = db.get(Paciente, dados.paciente.id)
        if paciente is None:
            paciente = Paciente(
                id=dados.paciente.id,
                nome=dados.paciente.nome,
                data_nascimento=dados.paciente.data_nascimento,
                sexo=dados.paciente.sexo,
            )
            db.add(paciente)
            db.flush()

        atendimento = Atendimento(
            id=dados.id,
            agente_id=usuario.id,
            paciente_id=paciente.id,
            data_hora=dados.data_hora,
            relato_texto=dados.relato_texto,
            relato_voz_path=dados.relato_voz_path,
            imagem_exantema_path=dados.imagem_exantema_path,
            localizacao=ponto,
            rua=dados.rua,
            bairro=dados.bairro,
            municipio=dados.municipio,
            probabilidade_dengue=prob,
            nivel_risco=nivel,
            status_sincronizacao=StatusSincronizacao.SINCRONIZADO,
            atualizado_em=dados.atualizado_em,
        )
        db.add(atendimento)
        db.flush()
        _gravar_resultado(db, atendimento, dados)
        return ItemSync(id=dados.id, situacao=SituacaoSync.CRIADO)

    # --- Atendimento já existe ---
    if existente.agente_id != usuario.id:
        return ItemSync(
            id=dados.id,
            situacao=SituacaoSync.REJEITADO,
            detalhe="Este atendimento pertence a outro agente.",
        )

    # RN06: o parecer validado pelo médico sempre prevalece
    if existente.status_validacao != StatusValidacao.PENDENTE:
        return ItemSync(
            id=dados.id,
            situacao=SituacaoSync.IGNORADO_PARECER_MEDICO,
            detalhe="Já existe parecer médico; o envio do celular foi ignorado.",
        )

    # RN06: entre dados do mesmo nível, vale o timestamp mais recente
    # (reenvio idêntico também cai aqui, o que evita duplicidade)
    if dados.atualizado_em <= existente.atualizado_em:
        return ItemSync(
            id=dados.id,
            situacao=SituacaoSync.IGNORADO_DESATUALIZADO,
            detalhe="O servidor já tem uma versão igual ou mais recente.",
        )

    existente.data_hora = dados.data_hora
    existente.relato_texto = dados.relato_texto
    existente.relato_voz_path = dados.relato_voz_path
    existente.imagem_exantema_path = dados.imagem_exantema_path
    existente.localizacao = ponto
    existente.rua = dados.rua
    existente.bairro = dados.bairro
    existente.municipio = dados.municipio
    existente.probabilidade_dengue = prob
    existente.nivel_risco = nivel
    existente.status_sincronizacao = StatusSincronizacao.SINCRONIZADO
    existente.atualizado_em = dados.atualizado_em
    db.flush()
    _gravar_resultado(db, existente, dados)
    return ItemSync(id=dados.id, situacao=SituacaoSync.ATUALIZADO)


@router.post("/sincronizar", response_model=SincronizacaoOut)
def sincronizar(
    lote: SincronizacaoIn,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(require_perfil(Perfil.ACS)),
):
    """Recebe os atendimentos pendentes do celular (RF07).

    - Idempotente: reenviar o mesmo atendimento não duplica (o id vem do celular).
    - RN03: atendimentos de ALTO risco são processados primeiro.
    - RN06: parecer médico prevalece; entre dados do mesmo nível, vale o mais recente.
    - Cada item é validado sozinho: um registro inválido vira REJEITADO e o resto segue.
    """
    resultados: list[ItemSync] = []
    validos: list[AtendimentoSync] = []

    for bruto in lote.atendimentos:
        try:
            validos.append(AtendimentoSync.model_validate(bruto))
        except ValidationError as erro:
            primeiro = erro.errors()[0]
            campo = ".".join(str(parte) for parte in primeiro["loc"])
            resultados.append(
                ItemSync(
                    id=_id_ou_none(bruto),
                    situacao=SituacaoSync.REJEITADO,
                    detalhe=f"{campo}: {primeiro['msg']}",
                )
            )

    # RN03: ALTO risco primeiro (a ordenação do Python é estável: mantém a ordem original no resto)
    validos.sort(key=lambda a: 0 if _nivel_final(a) == NivelRisco.ALTO else 1)

    processados: list[ItemSync] = []
    for dados in validos:
        try:
            with db.begin_nested():  # se um item falhar, só ele é desfeito
                processados.append(_processar(db, usuario, dados))
        except Exception:  # noqa: BLE001
            processados.append(
                ItemSync(
                    id=dados.id,
                    situacao=SituacaoSync.REJEITADO,
                    detalhe="Erro ao gravar este registro. Tente novamente.",
                )
            )
    db.commit()

    itens = processados + resultados  # os processados (ALTO primeiro) vêm antes dos inválidos
    por_situacao = lambda *s: sum(1 for i in itens if i.situacao in s)  # noqa: E731
    return SincronizacaoOut(
        ids_sincronizados=[i.id for i in itens if i.situacao != SituacaoSync.REJEITADO and i.id],
        itens=itens,
        criados=por_situacao(SituacaoSync.CRIADO),
        atualizados=por_situacao(SituacaoSync.ATUALIZADO),
        ignorados=por_situacao(SituacaoSync.IGNORADO_DESATUALIZADO, SituacaoSync.IGNORADO_PARECER_MEDICO),
        rejeitados=por_situacao(SituacaoSync.REJEITADO),
    )
