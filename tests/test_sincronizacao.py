"""Testes de integração da sincronização (RF07, RN02, RN03, RN05, RN06).

Precisam do banco rodando. Execute dentro do container:
    docker compose exec api pytest -v
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from app.core.database import SessionLocal
from app.core.security import hash_senha
from app.main import app
from app.models import Atendimento, Paciente, Perfil, StatusValidacao, Usuario

DOMINIO = "@teste.medgraph"
SENHA = "senha12345"
T1 = "2026-10-03T10:00:00-04:00"  # mais antigo
T2 = "2026-10-03T11:00:00-04:00"  # mais novo
URL = "/api/v1/sincronizar"


def _email(prefixo="sync"):
    return f"{prefixo}{uuid.uuid4().hex[:8]}{DOMINIO}"


def _login(client, email):
    r = client.post("/auth/login", data={"username": email, "password": SENHA})
    assert r.status_code == 200
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _item(**extra):
    base = {
        "id": str(uuid.uuid4()),
        "paciente": {"id": str(uuid.uuid4()), "nome": "Paciente Teste Sync", "sexo": "F"},
        "data_hora": T1,
        "atualizado_em": T1,
        "relato_texto": "Febre alta",
        "latitude": -3.1190,
        "longitude": -60.0217,
        "municipio": "Manaus",
    }
    base.update(extra)
    return base


def _enviar(client, headers, *itens):
    return client.post(URL, headers=headers, json={"atendimentos": list(itens)})


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    with SessionLocal() as db:
        ids = select(Usuario.id).where(Usuario.email.like(f"%{DOMINIO}"))
        db.execute(delete(Atendimento).where(Atendimento.agente_id.in_(ids)))
        db.execute(delete(Paciente).where(Paciente.nome.like("Paciente Teste%")))
        db.execute(delete(Usuario).where(Usuario.email.like(f"%{DOMINIO}")))
        db.commit()


@pytest.fixture(scope="module")
def gestor(client):
    email = _email("gestor")
    with SessionLocal() as db:
        db.add(Usuario(nome="Gestor Sync", email=email, senha_hash=hash_senha(SENHA), perfil=Perfil.GESTOR))
        db.commit()
    return _login(client, email)


def _criar_acs(client, gestor):
    email = _email("acs")
    r = client.post(
        "/auth/registrar",
        headers=gestor,
        json={"nome": "Agente Sync", "email": email, "senha": SENHA, "perfil": "ACS"},
    )
    assert r.status_code == 201
    return _login(client, email)


@pytest.fixture(scope="module")
def acs1(client, gestor):
    return _criar_acs(client, gestor)


@pytest.fixture(scope="module")
def acs2(client, gestor):
    return _criar_acs(client, gestor)


def test_sem_token_retorna_401(client):
    assert client.post(URL, json={"atendimentos": [_item()]}).status_code == 401


def test_gestor_nao_pode_sincronizar(client, gestor):
    assert _enviar(client, gestor, _item()).status_code == 403


def test_atendimento_novo_e_criado(client, acs1):
    item = _item()
    r = _enviar(client, acs1, item)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["criados"] == 1
    assert corpo["itens"][0]["situacao"] == "CRIADO"
    assert item["id"] in corpo["ids_sincronizados"]

    detalhe = client.get("/atendimentos/" + item["id"], headers=acs1)
    assert detalhe.status_code == 200
    assert detalhe.json()["status_sincronizacao"] == "SINCRONIZADO"
    assert abs(detalhe.json()["latitude"] - (-3.1190)) < 1e-6


def test_reenvio_nao_duplica(client, acs1):
    item = _item()
    assert _enviar(client, acs1, item).json()["criados"] == 1
    segundo = _enviar(client, acs1, item).json()
    assert segundo["criados"] == 0
    assert segundo["itens"][0]["situacao"] == "IGNORADO_DESATUALIZADO"
    assert item["id"] in segundo["ids_sincronizados"]  # o celular pode marcar como sincronizado

    ids = [a["id"] for a in client.get("/atendimentos?limit=200", headers=acs1).json()]
    assert ids.count(item["id"]) == 1


def test_versao_mais_recente_atualiza(client, acs1):
    item = _item(relato_texto="versão 1")
    _enviar(client, acs1, item)
    novo = dict(item, relato_texto="versão 2", atualizado_em=T2)
    r = _enviar(client, acs1, novo).json()
    assert r["atualizados"] == 1
    assert r["itens"][0]["situacao"] == "ATUALIZADO"
    assert client.get("/atendimentos/" + item["id"], headers=acs1).json()["relato_texto"] == "versão 2"


def test_versao_mais_antiga_e_ignorada(client, acs1):
    item = _item(relato_texto="versão nova", atualizado_em=T2)
    _enviar(client, acs1, item)
    antigo = dict(item, relato_texto="versão velha", atualizado_em=T1)
    r = _enviar(client, acs1, antigo).json()
    assert r["itens"][0]["situacao"] == "IGNORADO_DESATUALIZADO"
    assert client.get("/atendimentos/" + item["id"], headers=acs1).json()["relato_texto"] == "versão nova"


def test_rn06_parecer_medico_prevalece(client, acs1):
    item = _item(relato_texto="texto original")
    _enviar(client, acs1, item)
    with SessionLocal() as db:  # simula o médico validando (o endpoint do médico vem no passo 7)
        db.execute(
            update(Atendimento)
            .where(Atendimento.id == uuid.UUID(item["id"]))
            .values(status_validacao=StatusValidacao.VALIDADO, parecer_medico="Confirmado")
        )
        db.commit()
    novo = dict(item, relato_texto="tentativa de sobrescrever", atualizado_em=T2)
    r = _enviar(client, acs1, novo).json()
    assert r["itens"][0]["situacao"] == "IGNORADO_PARECER_MEDICO"
    assert client.get("/atendimentos/" + item["id"], headers=acs1).json()["relato_texto"] == "texto original"


def test_outro_agente_nao_sobrescreve(client, acs1, acs2):
    item = _item(relato_texto="do agente 1")
    _enviar(client, acs1, item)
    r = _enviar(client, acs2, dict(item, relato_texto="invasão", atualizado_em=T2)).json()
    assert r["itens"][0]["situacao"] == "REJEITADO"
    assert item["id"] not in r["ids_sincronizados"]
    assert client.get("/atendimentos/" + item["id"], headers=acs1).json()["relato_texto"] == "do agente 1"


def test_rn03_alto_risco_e_processado_primeiro(client, acs1):
    baixo1, baixo2 = _item(), _item()
    alto = _item(resultado={"score_probabilidade": 0.9, "sinais_alarme": ["sangramento de mucosas"]})
    r = _enviar(client, acs1, baixo1, baixo2, alto).json()
    assert r["itens"][0]["id"] == alto["id"]
    detalhe = client.get("/atendimentos/" + alto["id"], headers=acs1).json()
    assert detalhe["nivel_risco"] == "ALTO"  # RN02


def test_item_invalido_nao_derruba_o_lote(client, acs1):
    bom = _item()
    sem_latitude = _item()
    sem_latitude.pop("latitude")  # RN05
    r = _enviar(client, acs1, bom, sem_latitude).json()
    assert r["criados"] == 1
    assert r["rejeitados"] == 1
    assert bom["id"] in r["ids_sincronizados"]
    assert sem_latitude["id"] not in r["ids_sincronizados"]


def test_paciente_criado_offline_e_reaproveitado(client, acs1):
    paciente = {"id": str(uuid.uuid4()), "nome": "Paciente Teste Mesmo", "sexo": "M"}
    a, b = _item(paciente=paciente), _item(paciente=paciente)
    r = _enviar(client, acs1, a, b).json()
    assert r["criados"] == 2
    pa = client.get("/atendimentos/" + a["id"], headers=acs1).json()["paciente"]["id"]
    pb = client.get("/atendimentos/" + b["id"], headers=acs1).json()["paciente"]["id"]
    assert pa == pb == paciente["id"]


def test_data_sem_fuso_horario_e_rejeitada(client, acs1):
    r = _enviar(client, acs1, _item(atualizado_em="2026-10-03T10:00:00")).json()
    assert r["rejeitados"] == 1


def test_lote_vazio_retorna_422(client, acs1):
    assert client.post(URL, headers=acs1, json={"atendimentos": []}).status_code == 422
