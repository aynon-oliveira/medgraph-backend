"""Testes da gestão de usuários (passo 15).

Os testes de regras de senha e de token não precisam de banco; os demais precisam. Execute no container:
    docker compose exec api pytest tests/test_usuarios_gestao.py -v
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import criar_token, decodificar_token, hash_senha, token_vale_apos_troca_de_senha
from app.main import app
from app.models import Atendimento, Paciente, Perfil, Usuario
from app.schemas.usuario import problemas_da_senha

DOMINIO = "@gestao.medgraph"
SENHA = "SenhaInicial77"


# ---------- regras de senha e token (sem banco) ----------
def test_senha_boa_nao_tem_problemas():
    assert problemas_da_senha("Cajueiro-2026x", "maria@x.com", "Maria Souza") == []


@pytest.mark.parametrize(
    "senha",
    ["curta1", "somente-letras", "123456789012", "senha12345", "mariaa2026", "souza-2026a"],
)
def test_senhas_fracas_sao_recusadas(senha):
    assert problemas_da_senha(senha, "mariaa@x.com", "Maria Souza")


def test_senha_com_mais_de_72_bytes_e_recusada():
    assert problemas_da_senha("a1" * 40)


def test_token_novo_carrega_o_instante_de_emissao():
    token = criar_token(uuid.uuid4(), Perfil.ACS)
    assert decodificar_token(token)["emitido_em"] > 0


def test_token_anterior_a_troca_de_senha_nao_vale():
    antes = datetime.now(timezone.utc)
    payload = {"emitido_em": antes.timestamp()}
    assert token_vale_apos_troca_de_senha(payload, None) is True
    assert token_vale_apos_troca_de_senha(payload, antes + timedelta(seconds=1)) is False
    assert token_vale_apos_troca_de_senha(payload, antes - timedelta(seconds=1)) is True
    assert token_vale_apos_troca_de_senha({}, antes) is False  # token sem a marca (formato antigo)


# ---------- endpoints (com banco) ----------
def _email(prefixo):
    return f"{prefixo}{uuid.uuid4().hex[:8]}{DOMINIO}"


def _login(client, email, senha=SENHA):
    r = client.post("/auth/login", data={"username": email, "password": senha})
    return r


def _cab(r):
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    with SessionLocal() as db:
        ids = select(Usuario.id).where(Usuario.email.like(f"%{DOMINIO}"))
        db.execute(delete(Atendimento).where(Atendimento.agente_id.in_(ids)))
        db.execute(delete(Paciente).where(Paciente.nome.like("Paciente Gestao%")))
        db.execute(delete(Usuario).where(Usuario.email.like(f"%{DOMINIO}")))
        db.commit()


def _criar_no_banco(perfil, prefixo):
    email = _email(prefixo)
    with SessionLocal() as db:
        u = Usuario(nome=f"Fulano {prefixo}", email=email, senha_hash=hash_senha(SENHA), perfil=perfil)
        db.add(u)
        db.commit()
        return email, str(u.id)


@pytest.fixture(scope="module")
def gestor(client):
    email, uid = _criar_no_banco(Perfil.GESTOR, "gestor")
    return {"email": email, "id": uid, "cab": _cab(_login(client, email))}


def _novo_usuario(client, gestor, perfil="ACS", prefixo="acs"):
    email = _email(prefixo)
    r = client.post(
        "/auth/registrar",
        headers=gestor["cab"],
        json={"nome": "Usuario Gestao", "email": email, "senha": SENHA, "perfil": perfil},
    )
    assert r.status_code == 201
    return {"email": email, "id": r.json()["id"]}


# --- trocar senha ---
def test_trocar_senha_funciona_e_derruba_o_token_antigo(client, gestor):
    u = _novo_usuario(client, gestor)
    token_antigo = _cab(_login(client, u["email"]))

    r = client.post(
        "/auth/trocar-senha", headers=token_antigo, json={"senha_atual": SENHA, "senha_nova": "Cajueiro-2026x"}
    )
    assert r.status_code == 200

    assert client.get("/auth/me", headers=token_antigo).status_code == 401  # sessão antiga caiu
    assert _login(client, u["email"], SENHA).status_code == 401  # senha antiga não entra mais
    novo = _login(client, u["email"], "Cajueiro-2026x")
    assert novo.status_code == 200
    assert client.get("/auth/me", headers=_cab(novo)).status_code == 200  # a nova sessão vale


def test_trocar_senha_com_senha_atual_errada_retorna_400(client, gestor):
    u = _novo_usuario(client, gestor)
    cab = _cab(_login(client, u["email"]))
    r = client.post("/auth/trocar-senha", headers=cab, json={"senha_atual": "errada123", "senha_nova": "Cajueiro-2026x"})
    assert r.status_code == 400
    assert client.get("/auth/me", headers=cab).status_code == 200  # nada mudou


def test_trocar_senha_recusa_senha_fraca_ou_igual(client, gestor):
    u = _novo_usuario(client, gestor)
    cab = _cab(_login(client, u["email"]))
    assert client.post("/auth/trocar-senha", headers=cab, json={"senha_atual": SENHA, "senha_nova": "12345678"}).status_code == 422
    assert client.post("/auth/trocar-senha", headers=cab, json={"senha_atual": SENHA, "senha_nova": SENHA}).status_code == 422


def test_trocar_senha_exige_login(client):
    assert client.post("/auth/trocar-senha", json={"senha_atual": "a", "senha_nova": "Cajueiro-2026x"}).status_code == 401


# --- desativar / reativar ---
def test_desativar_bloqueia_login_e_derruba_o_token_na_hora(client, gestor):
    u = _novo_usuario(client, gestor)
    cab = _cab(_login(client, u["email"]))
    assert client.get("/auth/me", headers=cab).status_code == 200

    r = client.post(f"/usuarios/{u['id']}/desativar", headers=gestor["cab"])
    assert r.status_code == 200 and r.json()["ativo"] is False

    assert client.get("/auth/me", headers=cab).status_code == 401
    assert _login(client, u["email"]).status_code == 401


def test_reativar_devolve_o_acesso(client, gestor):
    u = _novo_usuario(client, gestor)
    client.post(f"/usuarios/{u['id']}/desativar", headers=gestor["cab"])
    r = client.post(f"/usuarios/{u['id']}/reativar", headers=gestor["cab"])
    assert r.status_code == 200 and r.json()["ativo"] is True
    assert _login(client, u["email"]).status_code == 200


def test_gestor_nao_desativa_a_propria_conta(client, gestor):
    assert client.post(f"/usuarios/{gestor['id']}/desativar", headers=gestor["cab"]).status_code == 400
    assert client.get("/auth/me", headers=gestor["cab"]).status_code == 200


def test_desativar_mantem_o_historico_de_atendimentos(client, gestor):
    u = _novo_usuario(client, gestor)
    cab = _cab(_login(client, u["email"]))
    r = client.post(
        "/atendimentos",
        headers=cab,
        json={
            "paciente": {"nome": f"Paciente Gestao {uuid.uuid4().hex[:6]}"},
            "data_hora": "2026-10-06T09:00:00-04:00",
            "latitude": -3.119,
            "longitude": -60.021,
        },
    )
    assert r.status_code == 201
    client.post(f"/usuarios/{u['id']}/desativar", headers=gestor["cab"])
    with SessionLocal() as db:
        assert db.get(Atendimento, uuid.UUID(r.json()["id"])) is not None  # o registro continua lá


# --- redefinir senha pelo gestor ---
def test_redefinir_senha_gera_temporaria_e_derruba_sessoes(client, gestor):
    u = _novo_usuario(client, gestor)
    cab = _cab(_login(client, u["email"]))

    r = client.post(f"/usuarios/{u['id']}/redefinir-senha", headers=gestor["cab"])
    assert r.status_code == 200
    temporaria = r.json()["senha_temporaria"]
    assert len(temporaria) == 12 and problemas_da_senha(temporaria) == []

    assert client.get("/auth/me", headers=cab).status_code == 401
    assert _login(client, u["email"], SENHA).status_code == 401
    assert _login(client, u["email"], temporaria).status_code == 200


def test_gestor_nao_redefine_a_propria_senha_por_aqui(client, gestor):
    assert client.post(f"/usuarios/{gestor['id']}/redefinir-senha", headers=gestor["cab"]).status_code == 400


# --- corrigir dados e listar ---
def test_corrigir_dados_do_usuario(client, gestor):
    u = _novo_usuario(client, gestor)
    r = client.patch(f"/usuarios/{u['id']}", headers=gestor["cab"], json={"nome": "Nome Corrigido", "microarea": "MA-07"})
    assert r.status_code == 200
    assert r.json()["nome"] == "Nome Corrigido" and r.json()["microarea"] == "MA-07"


def test_corrigir_nao_aceita_email_perfil_nem_corpo_vazio(client, gestor):
    u = _novo_usuario(client, gestor)
    assert client.patch(f"/usuarios/{u['id']}", headers=gestor["cab"], json={"perfil": "GESTOR"}).status_code == 422
    assert client.patch(f"/usuarios/{u['id']}", headers=gestor["cab"], json={"email": "x@y.zz"}).status_code == 422
    assert client.patch(f"/usuarios/{u['id']}", headers=gestor["cab"], json={}).status_code == 422
    assert client.patch(f"/usuarios/{u['id']}", headers=gestor["cab"], json={"nome": None}).status_code == 422


def test_listar_com_filtros(client, gestor):
    u = _novo_usuario(client, gestor, "MEDICO", "medico")
    client.post(f"/usuarios/{u['id']}/desativar", headers=gestor["cab"])
    r = client.get("/usuarios", headers=gestor["cab"], params={"perfil": "MEDICO", "ativo": "false", "busca": DOMINIO})
    assert r.status_code == 200
    assert u["id"] in [x["id"] for x in r.json()]
    ativos = client.get("/usuarios", headers=gestor["cab"], params={"ativo": "true", "busca": DOMINIO}).json()
    assert u["id"] not in [x["id"] for x in ativos]


def test_usuario_inexistente_retorna_404(client, gestor):
    falso = uuid.uuid4()
    assert client.get(f"/usuarios/{falso}", headers=gestor["cab"]).status_code == 404
    assert client.post(f"/usuarios/{falso}/desativar", headers=gestor["cab"]).status_code == 404


# --- permissões ---
def test_so_o_gestor_gerencia_usuarios(client, gestor):
    alvo = _novo_usuario(client, gestor)
    acs = _novo_usuario(client, gestor)
    cab_acs = _cab(_login(client, acs["email"]))
    assert client.get("/usuarios", headers=cab_acs).status_code == 403
    assert client.patch(f"/usuarios/{alvo['id']}", headers=cab_acs, json={"nome": "Hacker Nome"}).status_code == 403
    assert client.post(f"/usuarios/{alvo['id']}/desativar", headers=cab_acs).status_code == 403
    assert client.post(f"/usuarios/{alvo['id']}/redefinir-senha", headers=cab_acs).status_code == 403
    assert client.post(f"/usuarios/{alvo['id']}/desativar").status_code == 401
