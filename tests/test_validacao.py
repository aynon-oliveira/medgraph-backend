"""Testes de integração da validação médica (RF08) e da fila priorizada.

Precisam do banco rodando. Execute dentro do container:
    docker compose exec api pytest -v
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, or_, select

from app.core.database import SessionLocal
from app.core.security import hash_senha
from app.main import app
from app.models import Atendimento, Paciente, Perfil, Usuario

DOMINIO = "@teste.medgraph"
SENHA = "senha12345"
PARECER = {"decisao": "VALIDADO", "parecer": "Quadro compatível com dengue. Confirmo a classificação."}


def _email(prefixo="val"):
    return f"{prefixo}{uuid.uuid4().hex[:8]}{DOMINIO}"


def _login(client, email):
    r = client.post("/auth/login", data={"username": email, "password": SENHA})
    assert r.status_code == 200
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _payload(**extra):
    base = {
        "paciente": {"nome": f"Paciente Teste {uuid.uuid4().hex[:6]}", "sexo": "F"},
        "data_hora": "2026-10-03T10:30:00-04:00",
        "relato_texto": "Febre alta e dor retro-orbitária",
        "latitude": -3.1190,
        "longitude": -60.0217,
        "municipio": "Manaus",
    }
    base.update(extra)
    return base


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    with SessionLocal() as db:
        ids = select(Usuario.id).where(Usuario.email.like(f"%{DOMINIO}"))
        db.execute(delete(Atendimento).where(or_(Atendimento.agente_id.in_(ids), Atendimento.medico_id.in_(ids))))
        db.execute(delete(Paciente).where(Paciente.nome.like("Paciente Teste%")))
        db.execute(delete(Usuario).where(Usuario.email.like(f"%{DOMINIO}")))
        db.commit()


@pytest.fixture(scope="module")
def gestor(client):
    email = _email("gestor")
    with SessionLocal() as db:
        db.add(Usuario(nome="Gestor Val", email=email, senha_hash=hash_senha(SENHA), perfil=Perfil.GESTOR))
        db.commit()
    return _login(client, email)


def _criar_usuario(client, gestor, perfil, **extra):
    email = _email(perfil.lower())
    corpo = {"nome": f"{perfil} Val", "email": email, "senha": SENHA, "perfil": perfil}
    corpo.update(extra)
    assert client.post("/auth/registrar", headers=gestor, json=corpo).status_code == 201
    return _login(client, email)


@pytest.fixture(scope="module")
def acs(client, gestor):
    return _criar_usuario(client, gestor, "ACS")


@pytest.fixture(scope="module")
def medico1(client, gestor):
    return _criar_usuario(client, gestor, "MEDICO", crm="CRM-AM 1111")


@pytest.fixture(scope="module")
def medico2(client, gestor):
    return _criar_usuario(client, gestor, "MEDICO", crm="CRM-AM 2222")


def _novo_atendimento(client, acs, **extra):
    r = client.post("/atendimentos", headers=acs, json=_payload(**extra))
    assert r.status_code == 201
    return r.json()["id"]


def _validar(client, headers, atendimento_id, corpo=None):
    return client.post("/atendimentos/" + atendimento_id + "/validacao", headers=headers, json=corpo or PARECER)


def test_sem_token_retorna_401(client):
    assert client.post("/atendimentos/" + str(uuid.uuid4()) + "/validacao", json=PARECER).status_code == 401


def test_acs_e_gestor_nao_podem_validar(client, acs, gestor):
    atendimento_id = _novo_atendimento(client, acs)
    assert _validar(client, acs, atendimento_id).status_code == 403
    assert _validar(client, gestor, atendimento_id).status_code == 403


def test_medico_valida_e_registra_parecer_e_encaminhamento(client, acs, medico1):
    atendimento_id = _novo_atendimento(client, acs)
    corpo = dict(PARECER, encaminhamento="Encaminhar à UPA mais próxima")
    r = _validar(client, medico1, atendimento_id, corpo)
    assert r.status_code == 200
    dados = r.json()
    assert dados["status_validacao"] == "VALIDADO"
    assert dados["parecer_medico"] == corpo["parecer"]
    assert dados["encaminhamento"] == "Encaminhar à UPA mais próxima"
    assert dados["medico_id"] == client.get("/auth/me", headers=medico1).json()["id"]


def test_acs_ve_o_parecer_no_proprio_atendimento(client, acs, medico1):
    atendimento_id = _novo_atendimento(client, acs)
    _validar(client, medico1, atendimento_id)
    dados = client.get("/atendimentos/" + atendimento_id, headers=acs).json()
    assert dados["status_validacao"] == "VALIDADO"
    assert dados["parecer_medico"] == PARECER["parecer"]


def test_medico_pode_rejeitar_o_indicativo_da_ia(client, acs, medico1):
    atendimento_id = _novo_atendimento(client, acs)
    corpo = {"decisao": "REJEITADO", "parecer": "Não há critérios para dengue neste caso."}
    r = _validar(client, medico1, atendimento_id, corpo)
    assert r.status_code == 200
    assert r.json()["status_validacao"] == "REJEITADO"


def test_medico_pode_corrigir_o_nivel_de_risco(client, acs, medico1):
    atendimento_id = _novo_atendimento(client, acs)
    r = _validar(client, medico1, atendimento_id, dict(PARECER, nivel_risco="ALTO"))
    assert r.json()["nivel_risco"] == "ALTO"


def test_parecer_muito_curto_retorna_422(client, acs, medico1):
    atendimento_id = _novo_atendimento(client, acs)
    assert _validar(client, medico1, atendimento_id, {"decisao": "VALIDADO", "parecer": "ok"}).status_code == 422


def test_decisao_pendente_retorna_422(client, acs, medico1):
    atendimento_id = _novo_atendimento(client, acs)
    corpo = {"decisao": "PENDENTE", "parecer": "Ainda analisando o caso."}
    assert _validar(client, medico1, atendimento_id, corpo).status_code == 422


def test_atendimento_inexistente_retorna_404(client, medico1):
    assert _validar(client, medico1, str(uuid.uuid4())).status_code == 404


def test_outro_medico_nao_revalida_mas_o_mesmo_pode(client, acs, medico1, medico2):
    atendimento_id = _novo_atendimento(client, acs)
    assert _validar(client, medico1, atendimento_id).status_code == 200
    assert _validar(client, medico2, atendimento_id).status_code == 409

    ajuste = dict(PARECER, parecer="Parecer ajustado pelo mesmo médico após nova avaliação.")
    r = _validar(client, medico1, atendimento_id, ajuste)
    assert r.status_code == 200
    assert r.json()["parecer_medico"] == ajuste["parecer"]


def test_fila_mostra_alto_risco_primeiro_e_remove_validados(client, acs, medico1):
    baixo = _novo_atendimento(client, acs)
    alto = _novo_atendimento(
        client, acs, resultado={"score_probabilidade": 0.9, "sinais_alarme": ["vômitos persistentes"]}
    )

    fila = [a["id"] for a in client.get("/validacoes/fila?limit=200", headers=medico1).json()]
    assert baixo in fila and alto in fila
    assert fila.index(alto) < fila.index(baixo)

    _validar(client, medico1, alto)
    fila = [a["id"] for a in client.get("/validacoes/fila?limit=200", headers=medico1).json()]
    assert alto not in fila
    assert baixo in fila


def test_acs_nao_acessa_a_fila(client, acs):
    assert client.get("/validacoes/fila", headers=acs).status_code == 403


def test_rn06_parecer_validado_bloqueia_a_sincronizacao_do_celular(client, acs, medico1):
    item = {
        "id": str(uuid.uuid4()),
        "paciente": {"id": str(uuid.uuid4()), "nome": "Paciente Teste Sync", "sexo": "F"},
        "data_hora": "2026-10-03T10:00:00-04:00",
        "atualizado_em": "2026-10-03T10:00:00-04:00",
        "relato_texto": "texto original do celular",
        "latitude": -3.1190,
        "longitude": -60.0217,
    }
    url = "/api/v1/sincronizar"
    assert client.post(url, headers=acs, json={"atendimentos": [item]}).json()["criados"] == 1
    assert _validar(client, medico1, item["id"]).status_code == 200

    novo = dict(item, relato_texto="tentativa de sobrescrever", atualizado_em="2099-01-01T00:00:00-04:00")
    r = client.post(url, headers=acs, json={"atendimentos": [novo]}).json()
    assert r["itens"][0]["situacao"] == "IGNORADO_PARECER_MEDICO"
    assert client.get("/atendimentos/" + item["id"], headers=acs).json()["relato_texto"] == "texto original do celular"
