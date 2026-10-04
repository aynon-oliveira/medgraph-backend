from fastapi import FastAPI
from app.routers import auth, usuarios, atendimentos, validacoes, sincronizacao, health, mapa

app = FastAPI(title="MEDGRAPH-AM API")

app.include_router(auth.router)
app.include_router(usuarios.router)
app.include_router(atendimentos.router)
app.include_router(validacoes.router)
app.include_router(sincronizacao.router)
app.include_router(health.router)
app.include_router(mapa.router)
