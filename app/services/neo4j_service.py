"""Grafo epidemiológico no Neo4j (RF09).

O PostgreSQL é a fonte da verdade. O grafo é uma PROJEÇÃO dele: se o Neo4j ficar fora do ar,
a API continua funcionando e o grafo pode ser reconstruído depois (POST /grafo/reconstruir).

Nós:        NoPaciente, NoSintoma, NoLocalidade, NoFocoVetor
Relações:   APRESENTA (Paciente -> Sintoma), RESIDE_EM (Paciente -> Localidade),
            LOCALIZADO_EM (FocoVetor -> Localidade)

RN04: as relações nunca são apagadas, só atualizadas no tempo (propriedade atualizadoEm).
"""
import logging
import time
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import SessionLocal
from app.core.neo4j_client import neo4j_session
from app.models import Atendimento
from app.schemas.atendimento import normalizar_sintomas

logger = logging.getLogger("medgraph.grafo")

_RESTRICOES = [
    "CREATE CONSTRAINT paciente_unico IF NOT EXISTS FOR (n:NoPaciente) REQUIRE n.pacienteId IS UNIQUE",
    "CREATE CONSTRAINT sintoma_unico IF NOT EXISTS FOR (n:NoSintoma) REQUIRE n.nomeSintoma IS UNIQUE",
    "CREATE CONSTRAINT localidade_unica IF NOT EXISTS FOR (n:NoLocalidade) REQUIRE n.chave IS UNIQUE",
    "CREATE CONSTRAINT foco_unico IF NOT EXISTS FOR (n:NoFocoVetor) REQUIRE n.id IS UNIQUE",
]


def garantir_esquema(tentativas: int = 3) -> bool:
    """Cria as restrições de unicidade. O Neo4j pode demorar a subir, então tenta algumas vezes."""
    for tentativa in range(1, tentativas + 1):
        try:
            with neo4j_session() as session:
                for comando in _RESTRICOES:
                    session.run(comando).consume()
            return True
        except Exception:  # noqa: BLE001
            logger.warning("Neo4j ainda indisponível (tentativa %s de %s).", tentativa, tentativas)
            time.sleep(2)
    return False


# ---------------------------------------------------------------- localidade e projeção


def montar_localidade(municipio: str | None, bairro: str | None) -> dict | None:
    """Sem município não há localidade. A chave é 'município|bairro' em minúsculas."""
    if not municipio or not municipio.strip():
        return None
    mun = " ".join(municipio.strip().split())
    bai = " ".join(bairro.strip().split()) if bairro and bairro.strip() else ""
    return {
        "chave": f"{mun.lower()}|{bai.lower()}",
        "nome": f"{bai}, {mun}" if bai else mun,
        "municipio": mun,
        "bairro": bai or None,
    }


def construir_projecao(a: Atendimento) -> dict:
    """Monta (padrão Builder) os dados de nós e relações a partir de um atendimento."""
    alarmes = set(normalizar_sintomas(list(a.resultado.sinais_alarme or []))) if a.resultado else set()
    sintomas = {nome: False for nome in normalizar_sintomas(list(a.sintomas or []))}
    for nome in alarmes:
        sintomas[nome] = True  # sinal de alarme também entra como sintoma

    return {
        "paciente_id": str(a.paciente_id),
        "atendimento_id": str(a.id),
        "quando": a.data_hora.astimezone(timezone.utc),
        "agora": datetime.now(timezone.utc),
        "localidade": montar_localidade(a.municipio, a.bairro),
        "sintomas": [{"nome": nome, "alarme": alarme} for nome, alarme in sintomas.items()],
    }


def _gravar(tx, p: dict) -> None:
    tx.run("MERGE (:NoPaciente {pacienteId: $paciente_id})", paciente_id=p["paciente_id"])

    if p["localidade"]:
        tx.run(
            """
            MATCH (p:NoPaciente {pacienteId: $paciente_id})
            MERGE (l:NoLocalidade {chave: $chave})
              ON CREATE SET l.nome = $nome, l.municipio = $municipio, l.bairro = $bairro
            MERGE (p)-[r:RESIDE_EM]->(l)
              ON CREATE SET r.desde = $quando
            SET r.atualizadoEm = $agora, r.ultimoAtendimentoId = $atendimento_id
            """,
            paciente_id=p["paciente_id"],
            atendimento_id=p["atendimento_id"],
            quando=p["quando"],
            agora=p["agora"],
            **p["localidade"],
        )

    if p["sintomas"]:
        tx.run(
            """
            UNWIND $sintomas AS s
            MERGE (n:NoSintoma {nomeSintoma: s.nome})
              ON CREATE SET n.ehSinalAlarme = s.alarme
              ON MATCH SET n.ehSinalAlarme = (n.ehSinalAlarme OR s.alarme)
            WITH n
            MATCH (p:NoPaciente {pacienteId: $paciente_id})
            MERGE (p)-[a:APRESENTA]->(n)
              ON CREATE SET a.primeiraVez = $quando
            SET a.atendimentos = CASE
                  WHEN $atendimento_id IN coalesce(a.atendimentos, []) THEN a.atendimentos
                  ELSE coalesce(a.atendimentos, []) + $atendimento_id END,
                a.ultimaVez = CASE
                  WHEN a.ultimaVez IS NULL OR $quando > a.ultimaVez THEN $quando
                  ELSE a.ultimaVez END,
                a.atualizadoEm = $agora
            """,
            sintomas=p["sintomas"],
            paciente_id=p["paciente_id"],
            atendimento_id=p["atendimento_id"],
            quando=p["quando"],
            agora=p["agora"],
        )


def _projetar(db: Session, ids: list[uuid.UUID]) -> int:
    if not ids:
        return 0
    atendimentos = db.scalars(
        select(Atendimento).options(selectinload(Atendimento.resultado)).where(Atendimento.id.in_(ids))
    ).all()
    projecoes = [construir_projecao(a) for a in atendimentos]
    with neo4j_session() as session:
        for projecao in projecoes:
            session.execute_write(_gravar, projecao)
    return len(projecoes)


def projetar_atendimentos(db: Session, ids: list[uuid.UUID]) -> int:
    """Atualiza o grafo sem nunca derrubar quem chamou: se o Neo4j falhar, só registra o aviso."""
    try:
        return _projetar(db, ids)
    except Exception:  # noqa: BLE001
        logger.warning("Não foi possível atualizar o grafo; a API segue funcionando.", exc_info=True)
        return 0


def projetar_em_segundo_plano(ids: list[uuid.UUID]) -> None:
    """Roda depois de a resposta ser enviada: usa uma sessão própria e nunca derruba a API."""
    with SessionLocal() as db:
        projetar_atendimentos(db, ids)


def reconstruir_grafo(db: Session) -> int:
    """Reprojeta todos os atendimentos (idempotente). Aqui os erros aparecem para quem pediu."""
    ids = list(db.scalars(select(Atendimento.id)).all())
    return _projetar(db, ids)


# ---------------------------------------------------------------- focos do vetor


def criar_foco(tipo_criadouro: str, densidade: str, data_identificacao: date, municipio: str, bairro: str | None) -> dict:
    localidade = montar_localidade(municipio, bairro)
    foco_id = str(uuid.uuid4())
    with neo4j_session() as session:
        session.run(
            """
            MERGE (l:NoLocalidade {chave: $chave})
              ON CREATE SET l.nome = $nome, l.municipio = $municipio, l.bairro = $bairro
            CREATE (f:NoFocoVetor {
              id: $id, tipoCriadouro: $tipo, densidadeVetorial: $densidade, dataIdentificacao: $data})
            CREATE (f)-[:LOCALIZADO_EM {desde: $agora, atualizadoEm: $agora}]->(l)
            """,
            id=foco_id,
            tipo=tipo_criadouro,
            densidade=densidade,
            data=data_identificacao,
            agora=datetime.now(timezone.utc),
            **localidade,
        ).consume()
    return {"id": foco_id, "localidade": localidade["nome"], "localidade_chave": localidade["chave"]}


# ---------------------------------------------------------------- consultas


def _ler(consulta: str, **parametros) -> list[dict]:
    with neo4j_session() as session:
        return session.run(consulta, **parametros).data()


def resumo() -> dict:
    nos = _ler("MATCH (n) RETURN labels(n)[0] AS rotulo, count(*) AS total")
    relacoes = _ler("MATCH ()-[r]->() RETURN type(r) AS tipo, count(*) AS total")
    return {
        "nos": {linha["rotulo"]: linha["total"] for linha in nos},
        "relacoes": {linha["tipo"]: linha["total"] for linha in relacoes},
    }


def frequencia_sintomas(limite: int) -> list[dict]:
    return _ler(
        """
        MATCH (p:NoPaciente)-[:APRESENTA]->(s:NoSintoma)
        RETURN s.nomeSintoma AS sintoma, s.ehSinalAlarme AS sinal_alarme, count(DISTINCT p) AS pacientes
        ORDER BY pacientes DESC, sintoma
        LIMIT $limite
        """,
        limite=limite,
    )


def localidades(limite: int) -> list[dict]:
    return _ler(
        """
        MATCH (l:NoLocalidade)
        OPTIONAL MATCH (p:NoPaciente)-[:RESIDE_EM]->(l)
        WITH l, count(DISTINCT p) AS pacientes
        OPTIONAL MATCH (f:NoFocoVetor)-[:LOCALIZADO_EM]->(l)
        RETURN l.chave AS chave, l.nome AS nome, pacientes, count(DISTINCT f) AS focos
        ORDER BY pacientes DESC, focos DESC, nome
        LIMIT $limite
        """,
        limite=limite,
    )
