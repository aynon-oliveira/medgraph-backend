"""Testes de integração do login e dos perfis (RF01).

Precisam do banco rodando. Execute dentro do container:
    docker compose exec api pytest -v
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.database import SessionLocal
from app.core.security import hash_senha
from app.main import app
from app.models import Perfil, Usuario

DOMINIO = "@teste.medgraph"
SENHA = "senha12345"


def _email():
    return f"{uuid.uuid4().hex[:10]}{DOMINIO}"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # "with" dispara a criação das tabelas
        yield c
    # limpeza: apaga os usuários criados pelos testes
    with SessionLocal() as db:
        db.execute(delete(Usuario).where(Usuario.email.like(f"%{DOMINIO}")))
        db.commit()


@pytest.fixture(scope="module")
def gestor(client):
    email = _email()
    with SessionLocal() as db:
        db.add(Usuario(nome="Gestor de Teste", email=email, senha_hash=hash_senha(SENHA), perfil=Perfil.GESTOR))
        db.commit()
    return {"email": email, "token": _login(client, email, SENHA).json()["access_token"]}


def _login(client, email, senha):
    return client.post("/auth/login", data={"username": email, "password": senha})


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_login_com_senha_errada_retorna_401(client, gestor):
    assert _login(client, gestor["email"], "senha_errada").status_code == 401


def test_me_sem_token_retorna_401(client):
    assert client.get("/auth/me").status_code == 401


def test_me_com_token_devolve_o_usuario(client, gestor):
    r = client.get("/auth/me", headers=_auth(gestor["token"]))
    assert r.status_code == 200
    assert r.json()["perfil"] == "GESTOR"
    assert "senha" not in r.json() and "senha_hash" not in r.json()


def test_gestor_cadastra_acs_e_acs_faz_login(client, gestor):
    email = _email()
    r = client.post(
        "/auth/registrar",
        headers=_auth(gestor["token"]),
        json={"nome": "Agente Teste", "email": email, "senha": SENHA, "perfil": "ACS", "microarea": "MA-01"},
    )
    assert r.status_code == 201
    login = _login(client, email, SENHA)
    assert login.status_code == 200
    assert login.json()["perfil"] == "ACS"


def test_email_duplicado_retorna_409(client, gestor):
    email = _email()
    corpo = {"nome": "Medico Teste", "email": email, "senha": SENHA, "perfil": "MEDICO", "crm": "CRM-AM 1234"}
    assert client.post("/auth/registrar", headers=_auth(gestor["token"]), json=corpo).status_code == 201
    assert client.post("/auth/registrar", headers=_auth(gestor["token"]), json=corpo).status_code == 409


def test_acs_nao_pode_cadastrar_nem_listar_usuarios(client, gestor):
    email = _email()
    client.post(
        "/auth/registrar",
        headers=_auth(gestor["token"]),
        json={"nome": "Agente Dois", "email": email, "senha": SENHA, "perfil": "ACS"},
    )
    token_acs = _login(client, email, SENHA).json()["access_token"]

    r1 = client.post(
        "/auth/registrar",
        headers=_auth(token_acs),
        json={"nome": "Intruso", "email": _email(), "senha": SENHA, "perfil": "GESTOR"},
    )
    assert r1.status_code == 403
    assert client.get("/usuarios", headers=_auth(token_acs)).status_code == 403


def test_gestor_lista_usuarios(client, gestor):
    r = client.get("/usuarios", headers=_auth(gestor["token"]))
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_cadastro_sem_login_retorna_401_quando_ja_existem_usuarios(client, gestor):
    r = client.post(
        "/auth/registrar",
        json={"nome": "Sem Login", "email": _email(), "senha": SENHA, "perfil": "ACS"},
    )
    assert r.status_code == 401
