"""Paginação estável da lista de USUÁRIOS (passo 15): nomes iguais não podem repetir nem pular registros.

Precisam do banco. Execute no container:
    docker compose exec api pytest tests/test_paginacao_usuarios.py -v
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import hash_senha
from app.main import app
from app.models import Atendimento, Perfil, Usuario

DOMINIO = "@paguser.medgraph"
SENHA = "SenhaInicial77"
TOTAL = 9


def _login(client, email):
    r = client.post("/auth/login", data={"username": email, "password": SENHA})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _todas_as_paginas(client, url, cab, tamanho, params=None):
    vistos, offset = [], 0
    while True:
        r = client.get(url, headers=cab, params={**(params or {}), "limit": tamanho, "offset": offset})
        assert r.status_code == 200
        pagina = r.json()
        vistos.extend(x["id"] for x in pagina)
        if len(pagina) < tamanho:
            return vistos
        offset += tamanho


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    with SessionLocal() as db:
        ids = select(Usuario.id).where(Usuario.email.like(f"%{DOMINIO}"))
        db.execute(delete(Atendimento).where(Atendimento.agente_id.in_(ids)))
        db.execute(delete(Usuario).where(Usuario.email.like(f"%{DOMINIO}")))
        db.commit()


@pytest.fixture(scope="module")
def gestor(client):
    email = f"gestor{uuid.uuid4().hex[:8]}{DOMINIO}"
    with SessionLocal() as db:
        db.add(Usuario(nome="Gestor Pag", email=email, senha_hash=hash_senha(SENHA), perfil=Perfil.GESTOR))
        db.commit()
    return _login(client, email)


def test_listagem_de_usuarios_com_nome_igual_nao_repete_nem_pula(client, gestor):
    ids = set()
    for _ in range(TOTAL):
        email = f"igual{uuid.uuid4().hex[:8]}{DOMINIO}"
        r = client.post(
            "/auth/registrar",
            headers=gestor,
            json={"nome": "Mesmo Nome Pag", "email": email, "senha": SENHA, "perfil": "ACS"},
        )
        assert r.status_code == 201
        ids.add(r.json()["id"])

    for tamanho in (2, 3, 4):
        vistos = _todas_as_paginas(client, "/usuarios", gestor, tamanho, {"busca": "Mesmo Nome Pag"})
        assert len(vistos) == len(set(vistos)) == TOTAL, f"página de {tamanho}: repetiu ou pulou usuários"
        assert set(vistos) == ids
