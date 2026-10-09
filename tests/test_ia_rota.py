"""Testes da rota do modelo de risco (passo 22).  docker compose exec api pytest tests/test_ia_rota.py -v"""
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import hash_senha
from app.main import app
from app.models import Atendimento, Paciente, Perfil, Usuario

DOMINIO = "@iarota.medgraph"
SENHA = "senha12345"
MODELO = {
    "versao": "teste-rota", "tipo": "regressao_logistica", "intercepto": -1.0, "limiar": 0.4, "idade_padrao_anos": 30.0,
    "features": [
        {"nome": "febre", "tipo": "binaria", "coef": 1.2, "rotulo": "Febre"},
        {"nome": "idade_anos", "tipo": "numerica", "coef": -0.3, "media": 30.0, "desvio": 15.0, "rotulo": "Idade (anos)"},
    ],
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    with SessionLocal() as db:
        ids = select(Usuario.id).where(Usuario.email.like(f"%{DOMINIO}"))
        db.execute(delete(Atendimento).where(Atendimento.agente_id.in_(ids)))
        db.execute(delete(Paciente).where(Paciente.nome.like("Paciente IA%")))
        db.execute(delete(Usuario).where(Usuario.email.like(f"%{DOMINIO}")))
        db.commit()


@pytest.fixture(scope="module")
def cabecalho(client):
    email = f"acs{uuid.uuid4().hex[:8]}{DOMINIO}"
    with SessionLocal() as db:
        db.add(Usuario(nome="ACS IA", email=email, senha_hash=hash_senha(SENHA), perfil=Perfil.ACS))
        db.commit()
    r = client.post("/auth/login", data={"username": email, "password": SENHA})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_exige_login(client):
    assert client.get("/api/v1/ia/modelo").status_code == 401


def test_sem_modelo_treinado_retorna_404(client, cabecalho, tmp_path, monkeypatch):
    monkeypatch.setenv("MEDGRAPH_MODELO_RISCO", str(tmp_path / "nao_existe.json"))
    assert client.get("/api/v1/ia/modelo", headers=cabecalho).status_code == 404


def test_modelo_invalido_nao_e_servido(client, cabecalho, tmp_path, monkeypatch):
    arq = tmp_path / "ruim.json"
    arq.write_text(json.dumps({"tipo": "outra_coisa"}), encoding="utf-8")
    monkeypatch.setenv("MEDGRAPH_MODELO_RISCO", str(arq))
    assert client.get("/api/v1/ia/modelo", headers=cabecalho).status_code == 404


def test_entrega_o_modelo_para_qualquer_perfil_autenticado(client, cabecalho, tmp_path, monkeypatch):
    arq = tmp_path / "ok.json"
    arq.write_text(json.dumps(MODELO), encoding="utf-8")
    monkeypatch.setenv("MEDGRAPH_MODELO_RISCO", str(arq))
    r = client.get("/api/v1/ia/modelo", headers=cabecalho)
    assert r.status_code == 200
    assert r.json()["versao"] == "teste-rota"
    assert r.headers["cache-control"] == "no-cache"


def test_atendimento_guarda_qual_modelo_gerou_o_risco(client, cabecalho):
    r = client.post("/atendimentos", headers=cabecalho, json={
        "paciente": {"nome": f"Paciente IA {uuid.uuid4().hex[:6]}"},
        "data_hora": "2026-10-06T09:00:00-04:00", "latitude": -3.119, "longitude": -60.021, "municipio": "Manaus",
        "nivel_risco": "MODERADO",
        "resultado": {"score_probabilidade": 0.62, "sinais_alarme": [], "modelo_versao": "risco-dengue-lr-20261016"},
    })
    assert r.status_code == 201, r.text
    assert r.json()["resultado"]["modelo_versao"] == "risco-dengue-lr-20261016"
    lido = client.get(f"/atendimentos/{r.json()['id']}", headers=cabecalho)
    assert lido.json()["resultado"]["modelo_versao"] == "risco-dengue-lr-20261016"
