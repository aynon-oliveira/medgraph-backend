"""Configuração comum dos testes.

Os testes criam atendimentos de mentira. Como cada atendimento também vai para o grafo,
ao final da sessão removemos do Neo4j o que ficou "órfão" (sem correspondência no PostgreSQL).
Dados reais não são tocados: pacientes que existem no PostgreSQL permanecem no grafo.
"""
import logging

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.neo4j_client import fechar_driver, neo4j_session
from app.models import Paciente

logger = logging.getLogger("medgraph.testes")


@pytest.fixture(scope="session", autouse=True)
def limpar_grafo_de_testes():
    yield
    try:
        with SessionLocal() as db:
            ids_reais = [str(i) for i in db.scalars(select(Paciente.id)).all()]
        with neo4j_session() as session:
            session.run(
                "MATCH (p:NoPaciente) WHERE NOT p.pacienteId IN $ids DETACH DELETE p", ids=ids_reais
            ).consume()
            session.run("MATCH (n:NoSintoma) WHERE NOT (n)<-[:APRESENTA]-() DELETE n").consume()
            session.run("MATCH (l:NoLocalidade) WHERE NOT (l)--() DELETE l").consume()
    except Exception:  # noqa: BLE001
        logger.warning("Não foi possível limpar o grafo após os testes.", exc_info=True)
    finally:
        fechar_driver()


@pytest.fixture(autouse=True)
def _zerar_limitador_de_login():
    """Passo 28: o limite de tentativas de login não pode vazar de um teste para outro."""
    from app.core.limitador import limitador_login

    limitador_login.zerar()
    yield
