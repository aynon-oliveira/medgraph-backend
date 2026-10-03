from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

import app.models  # noqa: F401  (registra as tabelas)
from app.core.database import Base, engine
from app.routers import health


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Ativa a extensão geográfica e cria as tabelas na primeira execução.
    # (Depois podemos trocar por migrações com Alembic.)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="MEDGRAPH-AM API",
    description="Back-end da plataforma multimodal com IA e grafos para a Dengue no Amazonas.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router)
