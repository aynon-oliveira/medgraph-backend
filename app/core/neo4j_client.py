from contextlib import contextmanager
from functools import lru_cache

from neo4j import GraphDatabase

from app.core.config import settings


@lru_cache
def get_driver():
    """Um único driver para toda a API (ele gerencia as conexões sozinho)."""
    # Tempos curtos: se o Neo4j estiver fora do ar, a API não pode ficar esperando muito.
    return GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        connection_timeout=5,
        connection_acquisition_timeout=10,
        max_transaction_retry_time=3,
    )


@contextmanager
def neo4j_session():
    with get_driver().session() as session:
        yield session


def fechar_driver() -> None:
    if get_driver.cache_info().currsize:
        get_driver().close()
        get_driver.cache_clear()
