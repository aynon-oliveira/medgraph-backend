"""Testes do mapa de risco (RF10).

Precisam do PostgreSQL e do Neo4j rodando. Execute dentro do container:
    docker compose exec api pytest -v
"""
import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, or_, select

from app.core.database import SessionLocal
from app.core.neo4j_client import fechar_driver, neo4j_session
from app.core.security import hash_senha
from app.main import app
from app.models import Atendimento, Paciente, Perfil, Usuario
from app.services import neo4j_service

DOMINIO = "@teste.medgraph"
SENHA = "senha12345"

SUF = uuid.uuid4().hex[:8]
MUN = f"TesteMapa{SUF}"
LAT, LON = -3.1234567, -60.0234567  # as coordenadas saem arredondadas em 3 casas
LAT_ARRED, LON_ARRED = -3.123, -60.023
LAT_APROX, LON_APROX = -3.12, -60.02  # localidade com menos de 3 casos (passo 29)
_PACIENTES: list[str] = []


def _email(prefixo="mapa"):
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
        "relato_texto": "Febre alta",
        "latitude": LAT,
        "longitude": LON,
        "municipio": MUN,
        "bairro": "Centro",
    }
    base.update(extra)
    return base


@contextmanager
def _neo4j_fora_do_ar():
    raise RuntimeError("Neo4j fora do ar (simulado no teste)")
    yield  # pragma: no cover


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    try:
        with SessionLocal() as db:
            ids = select(Usuario.id).where(Usuario.email.like(f"%{DOMINIO}"))
            db.execute(delete(Atendimento).where(or_(Atendimento.agente_id.in_(ids), Atendimento.medico_id.in_(ids))))
            db.execute(delete(Paciente).where(Paciente.nome.like("Paciente Teste%")))
            db.execute(delete(Usuario).where(Usuario.email.like(f"%{DOMINIO}")))
            db.commit()
        with neo4j_session() as session:
            session.run("MATCH (p:NoPaciente) WHERE p.pacienteId IN $ids DETACH DELETE p", ids=_PACIENTES).consume()
            session.run("MATCH (f:NoFocoVetor) WHERE f.tipoCriadouro STARTS WITH 'teste-foco-' DETACH DELETE f").consume()
            session.run("MATCH (l:NoLocalidade) WHERE l.chave STARTS WITH 'testemapa' DETACH DELETE l").consume()
    finally:
        fechar_driver()


@pytest.fixture(scope="module")
def gestor(client):
    email = _email("gestor")
    with SessionLocal() as db:
        db.add(Usuario(nome="Gestor Mapa", email=email, senha_hash=hash_senha(SENHA), perfil=Perfil.GESTOR))
        db.commit()
    return _login(client, email)


def _criar_usuario(client, gestor, perfil, **extra):
    email = _email(perfil.lower())
    corpo = {"nome": f"{perfil} Mapa", "email": email, "senha": SENHA, "perfil": perfil}
    corpo.update(extra)
    assert client.post("/auth/registrar", headers=gestor, json=corpo).status_code == 201
    return _login(client, email)


@pytest.fixture(scope="module")
def acs(client, gestor):
    return _criar_usuario(client, gestor, "ACS")


@pytest.fixture(scope="module")
def medico(client, gestor):
    return _criar_usuario(client, gestor, "MEDICO", crm="CRM-AM 4444")


@pytest.fixture(scope="module")
def dados(client, acs):
    """Três atendimentos: A (alto risco) e B (sem risco definido) no Centro; C em outro bairro."""

    def criar(**extra):
        r = client.post("/atendimentos", headers=acs, json=_payload(**extra))
        assert r.status_code == 201
        _PACIENTES.append(r.json()["paciente"]["id"])
        return r.json()["id"]

    a = criar(resultado={"score_probabilidade": 0.9, "sinais_alarme": ["dor abdominal intensa"]})
    b = criar()
    c = criar(bairro="Cidade Nova", latitude=-3.0, longitude=-60.0)
    return {"a": a, "b": b, "c": c}


def _ids(resposta):
    return [f["properties"]["atendimento_id"] for f in resposta.json()["features"]]


# ------------------------------------------------------------------ permissões


def test_sem_token_retorna_401(client):
    assert client.get("/api/v1/mapa/atendimentos").status_code == 401
    assert client.get("/api/v1/mapa/densidade").status_code == 401


def test_acs_nao_acessa_o_mapa(client, acs):
    assert client.get("/api/v1/mapa/atendimentos", headers=acs).status_code == 403
    assert client.get("/api/v1/mapa/densidade", headers=acs).status_code == 403


# ------------------------------------------------------------------ mapa de atendimentos


def test_gestor_e_medico_veem_os_atendimentos_em_geojson(client, dados, gestor, medico):
    for headers in (gestor, medico):
        r = client.get("/api/v1/mapa/atendimentos?limit=5000", headers=headers)
        assert r.status_code == 200
        assert r.json()["type"] == "FeatureCollection"
        assert dados["a"] in _ids(r)


def test_privacidade_sem_paciente_e_com_coordenadas_arredondadas(client, dados, gestor):
    r = client.get("/api/v1/mapa/atendimentos?limit=5000", headers=gestor)
    feature = next(f for f in r.json()["features"] if f["properties"]["atendimento_id"] == dados["a"])
    assert feature["geometry"]["coordinates"] == [LON_APROX, LAT_APROX]  # Centro tem 2 casos (< 3)
    assert feature["properties"]["localizacao_aproximada"] is True
    propriedades = feature["properties"]
    assert "paciente_id" not in propriedades and "nome" not in propriedades
    assert propriedades["nivel_risco"] == "ALTO"
    assert propriedades["bairro"] == "Centro"


def test_filtro_por_nivel_de_risco(client, dados, gestor):
    r = client.get("/api/v1/mapa/atendimentos?nivel_risco=ALTO&limit=5000", headers=gestor)
    assert r.status_code == 200
    assert dados["a"] in _ids(r)
    assert dados["b"] not in _ids(r)
    assert all(f["properties"]["nivel_risco"] == "ALTO" for f in r.json()["features"])


def test_filtro_por_periodo(client, dados, gestor):
    futuro = client.get("/api/v1/mapa/atendimentos?desde=2099-01-01T00:00:00Z&limit=5000", headers=gestor)
    assert dados["a"] not in _ids(futuro)
    passado = client.get("/api/v1/mapa/atendimentos?ate=2000-01-01T00:00:00Z&limit=5000", headers=gestor)
    assert dados["a"] not in _ids(passado)
    dentro = client.get(
        "/api/v1/mapa/atendimentos?desde=2026-10-01T00:00:00Z&ate=2026-10-31T00:00:00Z&limit=5000", headers=gestor
    )
    assert dados["a"] in _ids(dentro)


def test_limite_de_resultados(client, dados, gestor):
    r = client.get("/api/v1/mapa/atendimentos?limit=1", headers=gestor)
    assert len(r.json()["features"]) == 1
    assert client.get("/api/v1/mapa/atendimentos?limit=0", headers=gestor).status_code == 422


def test_nome_antigo_da_rota_continua_funcionando(client, dados, gestor):
    r = client.get("/api/v1/mapa/focos?limit=5000", headers=gestor)
    assert r.status_code == 200
    assert dados["a"] in _ids(r)


# ------------------------------------------------------------------ densidade por localidade


def _localidade(resposta, nome):
    return next(f for f in resposta.json()["features"] if f["properties"]["localidade"] == nome)


def test_densidade_por_localidade_conta_casos_e_focos(client, dados, gestor, medico):
    r = client.get("/api/v1/mapa/densidade", headers=medico)
    assert r.status_code == 200
    assert r.json()["grafo_disponivel"] is True

    centro = _localidade(r, f"Centro, {MUN}")["properties"]
    assert centro["total_atendimentos"] == 2
    assert centro["alto_risco"] == 1
    assert centro["proporcao_alto_risco"] == 0.5
    assert centro["focos"] == 0
    assert _localidade(r, f"Cidade Nova, {MUN}")["properties"]["total_atendimentos"] == 1
    assert _localidade(r, f"Centro, {MUN}")["geometry"]["coordinates"] == [LON_APROX, LAT_APROX]

    foco = {
        "tipo_criadouro": f"teste-foco-{SUF}",
        "densidade_vetorial": "ALTA",
        "data_identificacao": "2026-10-01",
        "municipio": MUN,
        "bairro": "Centro",
    }
    assert client.post("/grafo/focos", headers=gestor, json=foco).status_code == 201

    depois = client.get("/api/v1/mapa/densidade", headers=gestor)
    assert _localidade(depois, f"Centro, {MUN}")["properties"]["focos"] == 1


def test_densidade_respeita_o_periodo(client, dados, gestor):
    r = client.get("/api/v1/mapa/densidade?desde=2099-01-01T00:00:00Z", headers=gestor)
    nomes = [f["properties"]["localidade"] for f in r.json()["features"]]
    assert f"Centro, {MUN}" not in nomes


def test_densidade_funciona_mesmo_com_o_neo4j_fora_do_ar(client, dados, gestor, monkeypatch):
    monkeypatch.setattr(neo4j_service, "neo4j_session", _neo4j_fora_do_ar)
    r = client.get("/api/v1/mapa/densidade", headers=gestor)
    assert r.status_code == 200
    assert r.json()["grafo_disponivel"] is False
    assert _localidade(r, f"Centro, {MUN}")["properties"]["focos"] is None


# ------------------------------------------------------------------ passo 29: localizacao aproximada


def test_localidade_com_poucos_casos_e_marcada_como_aproximada(client, dados, gestor):
    r = client.get("/api/v1/mapa/atendimentos?limit=5000", headers=gestor)
    ponto = next(f for f in r.json()["features"] if f["properties"]["atendimento_id"] == dados["c"])
    assert ponto["properties"]["localizacao_aproximada"] is True
    d = client.get("/api/v1/mapa/densidade", headers=gestor)
    assert _localidade(d, f"Cidade Nova, {MUN}")["properties"]["localizacao_aproximada"] is True


def test_localidade_com_3_casos_mantem_a_precisao_normal(client, acs, gestor):
    bairro = f"Grande{SUF}"
    ids = []
    for _ in range(3):
        r = client.post("/atendimentos", headers=acs, json=_payload(bairro=bairro))
        assert r.status_code == 201
        _PACIENTES.append(r.json()["paciente"]["id"])
        ids.append(r.json()["id"])
    r = client.get("/api/v1/mapa/atendimentos?limit=5000", headers=gestor)
    ponto = next(f for f in r.json()["features"] if f["properties"]["atendimento_id"] == ids[0])
    assert ponto["geometry"]["coordinates"] == [LON_ARRED, LAT_ARRED]
    assert ponto["properties"]["localizacao_aproximada"] is False
    # filtrar por risco nao muda a regra: ela vale para a localidade inteira
    so_alto = client.get("/api/v1/mapa/atendimentos?nivel_risco=ALTO&limit=5000", headers=gestor)
    assert ids[0] not in _ids(so_alto)
