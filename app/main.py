from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

import app.models  # noqa: F401  (registra as tabelas)
from app.core.config import settings
from app.core.database import Base, engine
from app.core.neo4j_client import fechar_driver
from app.routers import atendimentos, auth, grafo, health, mapa, sincronizacao, usuarios, validacoes
from app.services.neo4j_service import garantir_esquema


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 1) Extensão geográfica e tabelas: é isto que permite subir o projeto em um banco novo.
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.create_all(bind=engine)

    # 2) Colunas adicionadas depois da primeira versão (create_all não altera tabelas já criadas).
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE atendimentos ADD COLUMN IF NOT EXISTS encaminhamento TEXT"))
        conn.execute(
            text("ALTER TABLE atendimentos ADD COLUMN IF NOT EXISTS sintomas VARCHAR[] NOT NULL DEFAULT '{}'")
        )

    # 3) Restrições de unicidade do grafo. Se o Neo4j não responder, a API sobe mesmo assim.
    garantir_esquema()

    yield

    fechar_driver()


app = FastAPI(
    title="MEDGRAPH-AM API",
    description="Back-end da plataforma multimodal com IA e grafos para a Dengue no Amazonas.",
    version="0.7.0",
    lifespan=lifespan,
)

# CORS: sem isto o navegador bloqueia as telas (ACS e painel do gestor) de chamarem a API.
# Só os endereços da lista CORS_ORIGINS são aceitos; a autenticação é pelo cabeçalho Authorization
# (token Bearer), então não é preciso liberar cookies (allow_credentials fica desligado).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
    max_age=600,
)

app.include_router(auth.router)
app.include_router(usuarios.router)
app.include_router(atendimentos.router)
app.include_router(validacoes.router)
app.include_router(sincronizacao.router)
app.include_router(grafo.router)
app.include_router(mapa.router)
app.include_router(health.router)
