from fastapi import APIRouter, Depends
from neo4j import GraphDatabase
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db

router = APIRouter(tags=["Saúde da API"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    """Confere se a API, o PostgreSQL/PostGIS e o Neo4j estão respondendo."""
    estado = {"api": "ok", "postgres": "erro", "postgis": "erro", "neo4j": "erro"}

    try:
        db.execute(text("SELECT 1"))
        estado["postgres"] = "ok"
        db.execute(text("SELECT PostGIS_Version()"))
        estado["postgis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        estado["postgres_detalhe"] = str(exc)[:120]

    try:
        driver = GraphDatabase.driver(
            settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
        )
        driver.verify_connectivity()
        driver.close()
        estado["neo4j"] = "ok"
    except Exception as exc:  # noqa: BLE001
        estado["neo4j_detalhe"] = str(exc)[:120]

    return estado
