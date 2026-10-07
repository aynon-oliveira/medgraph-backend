"""Testes do grafo epidemiológico no Neo4j (RF09, RN04).

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
T1 = "2026-10-03T10:00:00-04:00"
T2 = "2026-10-03T11:00:00-04:00"

SUF = uuid.uuid4().hex[:8]
MUN = f"TesteGrafo{SUF}"  # a chave da localidade fica "testegrafo<sufixo>|bairro"
S1, S2, S3 = (f"teste-sintoma-{SUF}-{letra}" for letra in "abc")
_PACIENTES: list[str] = []  # ids dos pacientes criados aqui, para limpar o grafo no final


def _email(prefixo="grafo"):
    return f"{prefixo}{uuid.uuid4().hex[:8]}{DOMINIO}"


def _login(client, email):
    r = client.post("/auth/login", data={"username": email, "password": SENHA})
    assert r.status_code == 200
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _cypher(consulta, **parametros):
    with neo4j_session() as session:
        return session.run(consulta, **parametros).data()


def _payload(**extra):
    base = {
        "paciente": {"nome": f"Paciente Teste {uuid.uuid4().hex[:6]}", "sexo": "F"},
        "data_hora": T1,
        "relato_texto": "Febre alta",
        "latitude": -3.1190,
        "longitude": -60.0217,
        "municipio": MUN,
        "bairro": "Centro",
    }
    base.update(extra)
    return base


def _item_sync(**extra):
    base = {
        "id": str(uuid.uuid4()),
        "paciente": {"id": str(uuid.uuid4()), "nome": "Paciente Teste Grafo", "sexo": "F"},
        "data_hora": T1,
        "atualizado_em": T1,
        "latitude": -3.1190,
        "longitude": -60.0217,
        "municipio": MUN,
        "bairro": "Centro",
    }
    base.update(extra)
    _PACIENTES.append(base["paciente"]["id"])
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
            session.run("MATCH (n:NoSintoma) WHERE n.nomeSintoma STARTS WITH 'teste-sintoma-' DETACH DELETE n").consume()
            session.run("MATCH (f:NoFocoVetor) WHERE f.tipoCriadouro STARTS WITH 'teste-foco-' DETACH DELETE f").consume()
            session.run("MATCH (l:NoLocalidade) WHERE l.chave STARTS WITH 'testegrafo' DETACH DELETE l").consume()
    finally:
        fechar_driver()


@pytest.fixture(scope="module")
def gestor(client):
    email = _email("gestor")
    with SessionLocal() as db:
        db.add(Usuario(nome="Gestor Grafo", email=email, senha_hash=hash_senha(SENHA), perfil=Perfil.GESTOR))
        db.commit()
    return _login(client, email)


def _criar_usuario(client, gestor, perfil, **extra):
    email = _email(perfil.lower())
    corpo = {"nome": f"{perfil} Grafo", "email": email, "senha": SENHA, "perfil": perfil}
    corpo.update(extra)
    assert client.post("/auth/registrar", headers=gestor, json=corpo).status_code == 201
    return _login(client, email)


@pytest.fixture(scope="module")
def acs(client, gestor):
    return _criar_usuario(client, gestor, "ACS")


@pytest.fixture(scope="module")
def medico(client, gestor):
    return _criar_usuario(client, gestor, "MEDICO", crm="CRM-AM 3333")


def _novo_atendimento(client, acs, **extra):
    r = client.post("/atendimentos", headers=acs, json=_payload(**extra))
    assert r.status_code == 201
    corpo = r.json()
    _PACIENTES.append(corpo["paciente"]["id"])
    return corpo


def _sincronizar(client, acs, *itens):
    r = client.post("/api/v1/sincronizar", headers=acs, json={"atendimentos": list(itens)})
    assert r.status_code == 200
    return r.json()


# ------------------------------------------------------------------ projeção no grafo (RF09)


def test_atendimento_criado_vira_nos_e_relacoes(client, acs):
    atendimento = _novo_atendimento(
        client,
        acs,
        sintomas=[S1, S2],
        resultado={"score_probabilidade": 0.9, "sinais_alarme": [S3]},
    )
    pid = atendimento["paciente"]["id"]

    sintomas = _cypher(
        "MATCH (:NoPaciente {pacienteId: $pid})-[:APRESENTA]->(s:NoSintoma) "
        "RETURN s.nomeSintoma AS nome, s.ehSinalAlarme AS alarme",
        pid=pid,
    )
    assert {linha["nome"]: linha["alarme"] for linha in sintomas} == {S1: False, S2: False, S3: True}

    localidade = _cypher(
        "MATCH (:NoPaciente {pacienteId: $pid})-[:RESIDE_EM]->(l:NoLocalidade) RETURN l.nome AS nome", pid=pid
    )
    assert [linha["nome"] for linha in localidade] == [f"Centro, {MUN}"]


def test_sintomas_sao_padronizados_antes_de_gravar(client, acs):
    atendimento = _novo_atendimento(client, acs, sintomas=[f"  {S1.upper()}  ", S1, ""])
    assert atendimento["sintomas"] == [S1]  # minúsculas, sem espaços sobrando e sem repetição


def test_sincronizacao_tambem_alimenta_o_grafo(client, acs):
    item = _item_sync(sintomas=[S1])
    assert _sincronizar(client, acs, item)["criados"] == 1
    pid = item["paciente"]["id"]
    linhas = _cypher(
        "MATCH (:NoPaciente {pacienteId: $pid})-[a:APRESENTA]->(:NoSintoma {nomeSintoma: $nome}) "
        "RETURN count(a) AS n",
        pid=pid,
        nome=S1,
    )
    assert linhas == [{"n": 1}]


def test_rn04_relacao_nao_e_apagada_quando_sintoma_deixa_de_ser_informado(client, acs):
    item = _item_sync(sintomas=[S1, S2])
    _sincronizar(client, acs, item)
    nova_versao = dict(item, sintomas=[S1], atualizado_em=T2)
    assert _sincronizar(client, acs, nova_versao)["atualizados"] == 1

    linhas = _cypher(
        "MATCH (:NoPaciente {pacienteId: $pid})-[a:APRESENTA]->(s:NoSintoma) "
        "WHERE s.nomeSintoma IN [$s1, $s2] RETURN s.nomeSintoma AS nome",
        pid=item["paciente"]["id"],
        s1=S1,
        s2=S2,
    )
    assert {linha["nome"] for linha in linhas} == {S1, S2}  # S2 continua no histórico


def test_reconstruir_nao_duplica_relacoes(client, acs, gestor):
    item = _item_sync(sintomas=[S1])
    _sincronizar(client, acs, item)
    assert client.post("/grafo/reconstruir", headers=gestor).status_code == 200
    assert client.post("/grafo/reconstruir", headers=gestor).status_code == 200

    linhas = _cypher(
        "MATCH (:NoPaciente {pacienteId: $pid})-[a:APRESENTA]->(:NoSintoma {nomeSintoma: $nome}) "
        "RETURN count(a) AS n, size(a.atendimentos) AS atendimentos",
        pid=item["paciente"]["id"],
        nome=S1,
    )
    assert linhas == [{"n": 1, "atendimentos": 1}]


def test_reconstruir_recria_o_que_foi_apagado_do_grafo(client, acs, gestor, medico):
    atendimento = _novo_atendimento(client, acs, sintomas=[S1])
    pid = atendimento["paciente"]["id"]
    _cypher("MATCH (p:NoPaciente {pacienteId: $pid}) DETACH DELETE p", pid=pid)
    assert _cypher("MATCH (p:NoPaciente {pacienteId: $pid}) RETURN p", pid=pid) == []

    r = client.post("/grafo/reconstruir", headers=gestor)
    assert r.status_code == 200
    assert r.json()["atendimentos_projetados"] >= 1
    assert len(_cypher("MATCH (p:NoPaciente {pacienteId: $pid}) RETURN p", pid=pid)) == 1

    assert client.post("/grafo/reconstruir", headers=medico).status_code == 403


# ------------------------------------------------------------------ consultas e permissões


def test_resumo_do_grafo_por_perfil(client, acs, medico, gestor):
    _novo_atendimento(client, acs, sintomas=[S1])
    for headers in (medico, gestor):
        r = client.get("/grafo/resumo", headers=headers)
        assert r.status_code == 200
        assert r.json()["nos"].get("NoPaciente", 0) >= 1
        assert r.json()["relacoes"].get("RESIDE_EM", 0) >= 1
    assert client.get("/grafo/resumo", headers=acs).status_code == 403
    assert client.get("/grafo/resumo").status_code == 401


def test_frequencia_de_sintomas(client, acs, medico):
    _novo_atendimento(client, acs, sintomas=[S1], resultado={"score_probabilidade": 0.8, "sinais_alarme": [S3]})
    r = client.get("/grafo/sintomas/frequencia?limit=200", headers=medico)
    assert r.status_code == 200
    por_nome = {linha["sintoma"]: linha for linha in r.json()}
    assert por_nome[S1]["pacientes"] >= 1
    assert por_nome[S3]["sinal_alarme"] is True


def test_gestor_registra_foco_e_ele_aparece_na_localidade(client, acs, gestor, medico):
    _novo_atendimento(client, acs)  # garante um paciente na localidade
    corpo = {
        "tipo_criadouro": f"teste-foco-{SUF}",
        "densidade_vetorial": "ALTA",
        "data_identificacao": "2026-10-01",
        "municipio": MUN,
        "bairro": "Centro",
    }
    r = client.post("/grafo/focos", headers=gestor, json=corpo)
    assert r.status_code == 201
    assert r.json()["localidade"] == f"Centro, {MUN}"

    lista = client.get("/grafo/localidades?limit=200", headers=medico).json()
    centro = next(item for item in lista if item["nome"] == f"Centro, {MUN}")
    assert centro["focos"] == 1
    assert centro["pacientes"] >= 1

    assert client.post("/grafo/focos", headers=medico, json=corpo).status_code == 403
    assert client.post("/grafo/focos", headers=gestor, json=dict(corpo, densidade_vetorial="MUITO")).status_code == 422


# ------------------------------------------------------------------ Neo4j fora do ar


def test_falha_no_neo4j_nao_derruba_a_criacao_do_atendimento(client, acs, monkeypatch):
    monkeypatch.setattr(neo4j_service, "neo4j_session", _neo4j_fora_do_ar)
    r = client.post("/atendimentos", headers=acs, json=_payload(sintomas=[S1]))
    assert r.status_code == 201  # o PostgreSQL é a fonte da verdade; o grafo se refaz depois
    _PACIENTES.append(r.json()["paciente"]["id"])


def test_consultas_retornam_503_quando_o_neo4j_esta_fora_do_ar(client, medico, monkeypatch):
    monkeypatch.setattr(neo4j_service, "neo4j_session", _neo4j_fora_do_ar)
    assert client.get("/grafo/resumo", headers=medico).status_code == 503
