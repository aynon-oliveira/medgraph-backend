"""Paginação estável (correção do passo 14): com critérios de ordenação empatados, páginas seguidas não podem
repetir nem pular registros.

Precisam do banco. Execute no container:
    docker compose exec api pytest tests/test_paginacao.py -v
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import hash_senha
from app.main import app
from app.models import Atendimento, Paciente, Perfil, Usuario

DOMINIO = "@pag.medgraph"
SENHA = "SenhaInicial77"
TOTAL = 9
MESMA_HORA = "2026-10-06T09:00:00-04:00"  # todos empatam em data_hora de propósito


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
        db.execute(delete(Paciente).where(Paciente.nome.like("Paciente Pag%")))
        db.execute(delete(Usuario).where(Usuario.email.like(f"%{DOMINIO}")))
        db.commit()


@pytest.fixture(scope="module")
def gestor(client):
    email = f"gestor{uuid.uuid4().hex[:8]}{DOMINIO}"
    with SessionLocal() as db:
        db.add(Usuario(nome="Gestor Pag", email=email, senha_hash=hash_senha(SENHA), perfil=Perfil.GESTOR))
        db.commit()
    return _login(client, email)


def test_listagem_de_atendimentos_com_data_hora_igual_nao_repete_nem_pula(client, gestor):
    email = f"acs{uuid.uuid4().hex[:8]}{DOMINIO}"
    r = client.post(
        "/auth/registrar",
        headers=gestor,
        json={"nome": "Agente Pag", "email": email, "senha": SENHA, "perfil": "ACS"},
    )
    assert r.status_code == 201
    acs = _login(client, email)

    criados = set()
    for i in range(TOTAL):
        r = client.post(
            "/atendimentos",
            headers=acs,
            json={
                "paciente": {"nome": f"Paciente Pag {i}"},
                "data_hora": MESMA_HORA,
                "latitude": -3.119,
                "longitude": -60.021,
            },
        )
        assert r.status_code == 201
        criados.add(r.json()["id"])

    for tamanho in (2, 3, 4):
        vistos = _todas_as_paginas(client, "/atendimentos", acs, tamanho)
        assert len(vistos) == len(set(vistos)) == TOTAL, f"página de {tamanho}: repetiu ou pulou registros"
        assert set(vistos) == criados
