"""Testes de integração do CRUD de atendimentos (RF02, RN02, RN05).

Precisam do banco rodando. Execute dentro do container:
    docker compose exec api pytest -v
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import hash_senha
from app.main import app
from app.models import Atendimento, Paciente, Perfil, Usuario

DOMINIO = "@teste.medgraph"
SENHA = "senha12345"


def _email(prefixo="atd"):
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
        "relato_texto": "Febre alta e dor retro-orbitária há 3 dias",
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
        db.execute(delete(Atendimento).where(Atendimento.agente_id.in_(ids)))
        db.execute(delete(Paciente).where(Paciente.nome.like("Paciente Teste%")))
        db.execute(delete(Usuario).where(Usuario.email.like(f"%{DOMINIO}")))
        db.commit()


@pytest.fixture(scope="module")
def gestor(client):
    email = _email("gestor")
    with SessionLocal() as db:
        db.add(Usuario(nome="Gestor Atd", email=email, senha_hash=hash_senha(SENHA), perfil=Perfil.GESTOR))
        db.commit()
    return _login(client, email)


def _criar_acs(client, gestor):
    email = _email("acs")
    r = client.post(
        "/auth/registrar",
        headers=gestor,
        json={"nome": "Agente Atd", "email": email, "senha": SENHA, "perfil": "ACS"},
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
    assert client.post("/atendimentos", json=_payload()).status_code == 401
    assert client.get("/atendimentos").status_code == 401


def test_acs_cria_atendimento_com_paciente_e_coordenadas(client, acs1):
    r = client.post("/atendimentos", headers=acs1, json=_payload())
    assert r.status_code == 201
    corpo = r.json()
    assert abs(corpo["latitude"] - (-3.1190)) < 1e-6
    assert abs(corpo["longitude"] - (-60.0217)) < 1e-6
    assert corpo["paciente"]["id"]
    assert corpo["status_validacao"] == "PENDENTE"


def test_rn05_sem_coordenadas_retorna_422(client, acs1):
    sem_lat = _payload()
    sem_lat.pop("latitude")
    assert client.post("/atendimentos", headers=acs1, json=sem_lat).status_code == 422

    sem_lon = _payload()
    sem_lon.pop("longitude")
    assert client.post("/atendimentos", headers=acs1, json=sem_lon).status_code == 422


def test_coordenadas_fora_do_intervalo_retornam_422(client, acs1):
    assert client.post("/atendimentos", headers=acs1, json=_payload(latitude=95)).status_code == 422
    assert client.post("/atendimentos", headers=acs1, json=_payload(longitude=-200)).status_code == 422


def test_paciente_ou_paciente_id_obrigatorio(client, acs1):
    corpo = _payload()
    corpo.pop("paciente")
    assert client.post("/atendimentos", headers=acs1, json=corpo).status_code == 422


def test_rn02_sinal_de_alarme_classifica_como_alto(client, acs1):
    r = client.post(
        "/atendimentos",
        headers=acs1,
        json=_payload(
            nivel_risco="BAIXO",
            resultado={"score_probabilidade": 0.82, "sinais_alarme": ["dor abdominal intensa"], "recomendacao": "Encaminhar"},
        ),
    )
    assert r.status_code == 201
    corpo = r.json()
    assert corpo["nivel_risco"] == "ALTO"
    assert corpo["probabilidade_dengue"] == 0.82
    assert corpo["resultado"]["sinais_alarme"] == ["dor abdominal intensa"]


def test_reaproveita_paciente_ja_cadastrado(client, acs1):
    primeiro = client.post("/atendimentos", headers=acs1, json=_payload()).json()
    corpo = _payload()
    corpo.pop("paciente")
    corpo["paciente_id"] = primeiro["paciente"]["id"]
    r = client.post("/atendimentos", headers=acs1, json=corpo)
    assert r.status_code == 201
    assert r.json()["paciente"]["id"] == primeiro["paciente"]["id"]


def test_id_duplicado_retorna_409(client, acs1):
    novo_id = str(uuid.uuid4())
    assert client.post("/atendimentos", headers=acs1, json=_payload(id=novo_id)).status_code == 201
    assert client.post("/atendimentos", headers=acs1, json=_payload(id=novo_id)).status_code == 409


def test_gestor_nao_pode_criar_atendimento(client, gestor):
    assert client.post("/atendimentos", headers=gestor, json=_payload()).status_code == 403


def test_acs_lista_so_os_seus_e_gestor_lista_todos(client, acs1, acs2, gestor):
    meu = client.post("/atendimentos", headers=acs1, json=_payload()).json()
    outro = client.post("/atendimentos", headers=acs2, json=_payload()).json()

    ids_acs1 = {a["id"] for a in client.get("/atendimentos?limit=200", headers=acs1).json()}
    assert meu["id"] in ids_acs1
    assert outro["id"] not in ids_acs1

    # O gestor enxerga os atendimentos de todos os agentes. Consulta direta por id (não depende de o
    # banco ter mais ou menos de 200 registros, como dependia a leitura da primeira página da lista).
    assert client.get(f"/atendimentos/{meu['id']}", headers=gestor).status_code == 200
    assert client.get(f"/atendimentos/{outro['id']}", headers=gestor).status_code == 200


def test_acs_nao_acessa_atendimento_de_outro_agente(client, acs1, acs2):
    do_acs1 = client.post("/atendimentos", headers=acs1, json=_payload()).json()
    url = "/atendimentos/" + do_acs1["id"]
    assert client.get(url, headers=acs1).status_code == 200
    assert client.get(url, headers=acs2).status_code == 404


def test_filtro_por_nivel_de_risco(client, acs1):
    alto = client.post(
        "/atendimentos",
        headers=acs1,
        json=_payload(resultado={"score_probabilidade": 0.9, "sinais_alarme": ["sangramento"]}),
    ).json()
    r = client.get("/atendimentos?nivel_risco=ALTO&limit=200", headers=acs1)
    assert r.status_code == 200
    assert alto["id"] in {a["id"] for a in r.json()}
    assert all(a["nivel_risco"] == "ALTO" for a in r.json())


def test_gestor_ve_o_atendimento_sem_identificar_o_paciente(client, acs1, gestor):
    """Passo 29 (LGPD): o Gestor trabalha com dados agregados; o ACS continua vendo a ficha completa."""
    nome = f"Paciente Teste {uuid.uuid4().hex[:6]}"
    criado = client.post(
        "/atendimentos",
        headers=acs1,
        json=_payload(
            paciente={"nome": nome, "sexo": "F", "data_nascimento": "1990-05-17"},
            rua="Rua das Flores, 123",
            latitude=-3.1234567,
            longitude=-60.0234567,
        ),
    ).json()

    do_acs = client.get(f"/atendimentos/{criado['id']}", headers=acs1).json()
    assert do_acs["paciente"]["nome"] == nome and do_acs["rua"] == "Rua das Flores, 123"

    g = client.get(f"/atendimentos/{criado['id']}", headers=gestor).json()
    assert g["paciente"]["nome"] != nome and g["paciente"]["nome"].startswith("Paciente ")
    assert g["paciente"]["data_nascimento"] is None
    assert g["relato_texto"] is None and g["rua"] is None
    assert (g["longitude"], g["latitude"]) == (-60.02, -3.12)
    assert g["sintomas"] == do_acs["sintomas"] and g["nivel_risco"] == do_acs["nivel_risco"]

    lista = client.get("/atendimentos?limit=200", headers=gestor)
    assert lista.status_code == 200
    assert nome not in lista.text and "Rua das Flores" not in lista.text
