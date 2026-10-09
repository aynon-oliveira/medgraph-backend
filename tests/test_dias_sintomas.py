"""Testes do campo "dias desde o início dos sintomas" (passo 26).
Execute dentro do container:  docker compose exec api pytest tests/test_dias_sintomas.py -v
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import hash_senha
from app.main import app
from app.models import Atendimento, Paciente, Perfil, RegistroAcesso, Usuario
from app.services import ia_risco

DOMINIO = "@dias.medgraph"
SENHA = "senha12345"


# ---------- modelo (sem banco) ----------
def _modelo(com_dias=True):
    feats = [
        {"nome": "febre", "coef": 0.5, "tipo": "binaria", "rotulo": "Febre"},
        {"nome": "idade_anos", "coef": 0.1, "tipo": "numerica", "media": 30.0, "desvio": 10.0, "rotulo": "Idade (anos)"},
    ]
    if com_dias:
        feats.append({"nome": "dias_sintomas", "coef": 0.4, "tipo": "numerica", "media": 4.0, "desvio": 2.0,
                      "rotulo": "Dias desde o início dos sintomas"})
    m = {"tipo": "regressao_logistica", "intercepto": -3.0, "features": feats, "limiar": 0.05, "idade_padrao_anos": 30.0}
    if com_dias:
        m["dias_padrao"] = 4.0
    return m


def test_modelo_com_dias_e_valido():
    ia_risco.validar(_modelo(True))


def test_mais_dias_aumentam_a_pontuacao():
    m = _modelo()
    assert ia_risco.pontuar(m, ["febre"], 30, "F", 10) > ia_risco.pontuar(m, ["febre"], 30, "F", 2)


def test_dias_ausentes_ou_invalidos_usam_o_valor_tipico():
    m = _modelo()
    tipico = ia_risco.pontuar(m, ["febre"], 30, "F", None)
    assert tipico == pytest.approx(ia_risco.pontuar(m, ["febre"], 30, "F", 4))   # dias_padrao = 4
    for ruim in (-1, 31, 99):
        assert ia_risco.pontuar(m, ["febre"], 30, "F", ruim) == pytest.approx(tipico)


def test_dias_acima_de_14_contam_como_14_como_no_treino():
    m = _modelo()
    assert ia_risco.pontuar(m, ["febre"], 30, "F", 25) == pytest.approx(ia_risco.pontuar(m, ["febre"], 30, "F", 14))


def test_modelo_antigo_sem_dias_ignora_o_dado():
    m = _modelo(com_dias=False)
    assert ia_risco.pontuar(m, ["febre"], 30, "F", 10) == pytest.approx(ia_risco.pontuar(m, ["febre"], 30, "F", None))


def test_idade_continua_funcionando_com_dias_no_modelo():
    m = _modelo()
    assert ia_risco.pontuar(m, ["febre"], 60, "F", 4) > ia_risco.pontuar(m, ["febre"], 10, "F", 4)


# ---------- API (com banco) ----------
def _email(p):
    return f"{p}{uuid.uuid4().hex[:8]}{DOMINIO}"


def _login(client, email):
    r = client.post("/auth/login", data={"username": email, "password": SENHA})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    with SessionLocal() as db:
        ids = select(Usuario.id).where(Usuario.email.like(f"%{DOMINIO}"))
        db.execute(delete(RegistroAcesso).where(RegistroAcesso.usuario_id.in_(ids)))
        db.execute(delete(Atendimento).where(Atendimento.agente_id.in_(ids)))
        db.execute(delete(Paciente).where(Paciente.nome.like("Paciente Dias%")))
        db.execute(delete(Usuario).where(Usuario.email.like(f"%{DOMINIO}")))
        db.commit()


@pytest.fixture(scope="module")
def acs(client):
    email = _email("gestor")
    with SessionLocal() as db:
        db.add(Usuario(nome="Gestor Dias", email=email, senha_hash=hash_senha(SENHA), perfil=Perfil.GESTOR))
        db.commit()
    gestor = _login(client, email)
    e2 = _email("acs")
    r = client.post("/auth/registrar", headers=gestor, json={"nome": "ACS Dias", "email": e2, "senha": SENHA, "perfil": "ACS"})
    assert r.status_code == 201
    return _login(client, e2)


def _corpo(**extra):
    base = {"paciente": {"nome": f"Paciente Dias {uuid.uuid4().hex[:6]}", "sexo": "F"},
            "data_hora": "2026-10-06T09:00:00-04:00", "latitude": -3.119, "longitude": -60.021,
            "municipio": "Manaus", "bairro": "Centro", "sintomas": ["febre"], "nivel_risco": "BAIXO"}
    base.update(extra)
    return base


def test_atendimento_guarda_e_devolve_os_dias(client, acs):
    r = client.post("/atendimentos", headers=acs, json=_corpo(dias_sintomas=5))
    assert r.status_code == 201 and r.json()["dias_sintomas"] == 5
    assert client.get("/atendimentos/" + r.json()["id"], headers=acs).json()["dias_sintomas"] == 5


def test_dias_e_opcional_e_zero_vale(client, acs):
    r = client.post("/atendimentos", headers=acs, json=_corpo())
    assert r.status_code == 201 and r.json()["dias_sintomas"] is None
    r0 = client.post("/atendimentos", headers=acs, json=_corpo(dias_sintomas=0))
    assert r0.status_code == 201 and r0.json()["dias_sintomas"] == 0


@pytest.mark.parametrize("ruim", [-1, 31, 100])
def test_dias_fora_do_intervalo_e_recusado(client, acs, ruim):
    assert client.post("/atendimentos", headers=acs, json=_corpo(dias_sintomas=ruim)).status_code == 422


def _item_sync(**extra):
    item = {"id": str(uuid.uuid4()),
            "paciente": {"id": str(uuid.uuid4()), "nome": "Paciente Dias Sync", "sexo": "F"},
            "data_hora": "2026-10-03T10:00:00-04:00", "atualizado_em": "2026-10-03T10:00:00-04:00",
            "latitude": -3.119, "longitude": -60.021}
    item.update(extra)
    return item


def test_sincronizacao_grava_os_dias(client, acs):
    item = _item_sync(dias_sintomas=3)
    r = client.post("/api/v1/sincronizar", headers=acs, json={"atendimentos": [item]}).json()
    assert r["criados"] == 1
    assert client.get("/atendimentos/" + item["id"], headers=acs).json()["dias_sintomas"] == 3


def test_sincronizacao_sem_dias_continua_funcionando(client, acs):
    """O aplicativo antigo (sem o campo) não pode quebrar."""
    item = _item_sync()
    assert client.post("/api/v1/sincronizar", headers=acs, json={"atendimentos": [item]}).json()["criados"] == 1
    assert client.get("/atendimentos/" + item["id"], headers=acs).json()["dias_sintomas"] is None


def test_sincronizacao_recusa_dias_invalidos_sem_derrubar_o_lote(client, acs):
    bom, ruim = _item_sync(dias_sintomas=2), _item_sync(dias_sintomas=99)
    r = client.post("/api/v1/sincronizar", headers=acs, json={"atendimentos": [bom, ruim]}).json()
    assert r["criados"] == 1 and r["rejeitados"] == 1


def test_reenvio_atualizado_troca_os_dias(client, acs):
    item = _item_sync(dias_sintomas=2)
    client.post("/api/v1/sincronizar", headers=acs, json={"atendimentos": [item]})
    novo = dict(item, dias_sintomas=6, atualizado_em="2026-10-03T11:00:00-04:00")
    assert client.post("/api/v1/sincronizar", headers=acs, json={"atendimentos": [novo]}).json()["atualizados"] == 1
    assert client.get("/atendimentos/" + item["id"], headers=acs).json()["dias_sintomas"] == 6
