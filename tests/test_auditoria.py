"""Testes do registro de acessos (passo 17, LGPD).

Os testes sem banco rodam em qualquer lugar; os de endpoint precisam do banco. Execute no container:
    docker compose exec api pytest tests/test_auditoria.py -v
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, or_, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_senha
from app.main import app
from app.models import Atendimento, Paciente, Perfil, RegistroAcesso, Usuario
from app.services import auditoria_service as aud

DOMINIO = "@audit.medgraph"
SENHA = "SenhaInicial77"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


# ---------- regras do serviço (sem banco) ----------
class _Usuario:
    def __init__(self, perfil):
        self.id, self.perfil = uuid.uuid4(), perfil


class _BancoFalso:
    def __init__(self, falha=False):
        self.falha, self.adicionados, self.commits, self.rollbacks = falha, [], 0, 0

    def add(self, obj):
        if self.falha:
            raise RuntimeError("banco fora do ar")
        self.adicionados.append(obj)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_acs_nao_gera_registro():
    db = _BancoFalso()
    aud.registrar_acesso(db, _Usuario(Perfil.ACS), aud.LER_ATENDIMENTO, "atendimento", uuid.uuid4())
    assert db.adicionados == [] and db.commits == 0


def test_medico_gera_registro_com_referencia_e_sem_conteudo():
    db = _BancoFalso()
    alvo = uuid.uuid4()
    aud.registrar_acesso(db, _Usuario(Perfil.MEDICO), aud.LER_ATENDIMENTO, "atendimento", alvo, detalhe="x" * 500)
    r = db.adicionados[0]
    assert db.commits == 1 and r.acao == "LER_ATENDIMENTO" and r.recurso_id == alvo and r.perfil == "MEDICO"
    assert len(r.detalhe) == 200  # nunca grava textos longos


def test_falha_no_registro_nao_derruba_a_consulta():
    db = _BancoFalso(falha=True)
    aud.registrar_acesso(db, _Usuario(Perfil.GESTOR), aud.VER_AUDITORIA, "auditoria")  # não levanta erro
    assert db.rollbacks == 1


# ---------- endpoints (com banco) ----------
def _login(client, email):
    r = client.post("/auth/login", data={"username": email, "password": SENHA})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module", autouse=True)
def pasta_temporaria(tmp_path_factory):
    antigo = settings.UPLOAD_DIR
    settings.UPLOAD_DIR = str(tmp_path_factory.mktemp("uploads"))
    yield
    settings.UPLOAD_DIR = antigo


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    with SessionLocal() as db:
        ids = select(Usuario.id).where(Usuario.email.like(f"%{DOMINIO}"))
        db.execute(delete(RegistroAcesso).where(RegistroAcesso.usuario_id.in_(ids)))
        db.execute(delete(Atendimento).where(or_(Atendimento.agente_id.in_(ids), Atendimento.medico_id.in_(ids))))
        db.execute(delete(Paciente).where(Paciente.nome.like("Paciente Audit%")))
        db.execute(delete(Usuario).where(Usuario.email.like(f"%{DOMINIO}")))
        db.commit()


@pytest.fixture(scope="module")
def gestor(client):
    email = f"gestor{uuid.uuid4().hex[:8]}{DOMINIO}"
    with SessionLocal() as db:
        u = Usuario(nome="Gestor Audit", email=email, senha_hash=hash_senha(SENHA), perfil=Perfil.GESTOR)
        db.add(u)
        db.commit()
        uid = str(u.id)
    return {"cab": _login(client, email), "id": uid}


def _novo(client, gestor, perfil, **extra):
    email = f"{perfil.lower()}{uuid.uuid4().hex[:8]}{DOMINIO}"
    corpo = {"nome": f"{perfil} Audit", "email": email, "senha": SENHA, "perfil": perfil, **extra}
    r = client.post("/auth/registrar", headers=gestor["cab"], json=corpo)
    assert r.status_code == 201
    return {"cab": _login(client, email), "id": r.json()["id"]}


@pytest.fixture(scope="module")
def acs(client, gestor):
    return _novo(client, gestor, "ACS")


@pytest.fixture(scope="module")
def medico(client, gestor):
    return _novo(client, gestor, "MEDICO", crm="CRM-AM 7777")


@pytest.fixture(scope="module")
def atendimento_id(client, acs):
    r = client.post(
        "/atendimentos",
        headers=acs["cab"],
        json={
            "paciente": {"nome": f"Paciente Audit {uuid.uuid4().hex[:6]}"},
            "data_hora": "2026-10-06T09:00:00-04:00",
            "latitude": -3.119,
            "longitude": -60.021,
        },
    )
    assert r.status_code == 201
    aid = r.json()["id"]
    assert client.put(f"/atendimentos/{aid}/midia/foto", headers=acs["cab"], files={"arquivo": ("a.jpg", JPEG, "image/jpeg")}).status_code == 200
    return aid


def _acessos(client, gestor, **params):
    r = client.get("/auditoria/acessos", headers=gestor["cab"], params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_medico_abrir_atendimento_fica_registrado(client, gestor, medico, atendimento_id):
    assert client.get(f"/atendimentos/{atendimento_id}", headers=medico["cab"]).status_code == 200
    regs = _acessos(client, gestor, recurso_id=atendimento_id, acao="LER_ATENDIMENTO")
    assert len(regs) == 1
    r = regs[0]
    assert r["usuario_id"] == medico["id"] and r["perfil"] == "MEDICO" and r["usuario_nome"] == "MEDICO Audit"
    assert r["recurso"] == "atendimento" and r["ip"]


def test_acs_ver_o_proprio_atendimento_nao_gera_registro(client, gestor, acs, atendimento_id):
    antes = len(_acessos(client, gestor, usuario_id=acs["id"]))
    assert client.get(f"/atendimentos/{atendimento_id}", headers=acs["cab"]).status_code == 200
    assert client.get("/atendimentos", headers=acs["cab"]).status_code == 200
    assert len(_acessos(client, gestor, usuario_id=acs["id"])) == antes == 0


def test_baixar_foto_e_ver_fila_e_validar_ficam_registrados(client, gestor, medico, atendimento_id):
    assert client.get(f"/atendimentos/{atendimento_id}/midia/foto", headers=medico["cab"]).status_code == 200
    assert client.get("/validacoes/fila", headers=medico["cab"]).status_code == 200
    r = client.post(
        f"/atendimentos/{atendimento_id}/validacao",
        headers=medico["cab"],
        json={"decisao": "VALIDADO", "parecer": "Quadro compatível, confirmo."},
    )
    assert r.status_code == 200
    acoes = {x["acao"]: x for x in _acessos(client, gestor, usuario_id=medico["id"], limit=200)}
    assert acoes["BAIXAR_MIDIA"]["detalhe"] == "foto" and acoes["BAIXAR_MIDIA"]["recurso_id"] == atendimento_id
    assert "VER_FILA_VALIDACAO" in acoes
    assert acoes["VALIDAR_ATENDIMENTO"]["detalhe"] == "VALIDADO"


def test_medico_listar_registra_so_a_quantidade(client, gestor, medico, atendimento_id):
    assert client.get("/atendimentos", headers=medico["cab"], params={"limit": 2}).status_code == 200
    regs = _acessos(client, gestor, usuario_id=medico["id"], acao="LISTAR_ATENDIMENTOS")
    assert regs and regs[0]["detalhe"].endswith("registros") and regs[0]["recurso_id"] is None


def test_o_registro_nao_guarda_conteudo_do_paciente(client, gestor, medico, atendimento_id):
    regs = _acessos(client, gestor, usuario_id=medico["id"], limit=200)
    texto = str(regs).lower()
    assert "paciente audit" not in texto and "parecer" not in texto and "quadro compat" not in texto
    assert set(regs[0]) == {"id", "criado_em", "usuario_id", "usuario_nome", "perfil", "acao", "recurso", "recurso_id", "detalhe", "ip"}


def test_so_o_gestor_consulta_a_auditoria(client, gestor, acs, medico):
    assert client.get("/auditoria/acessos", headers=acs["cab"]).status_code == 403
    assert client.get("/auditoria/acessos", headers=medico["cab"]).status_code == 403
    assert client.get("/auditoria/acessos").status_code == 401


def test_gestor_consultar_a_auditoria_tambem_fica_registrado(client, gestor):
    _acessos(client, gestor)
    regs = _acessos(client, gestor, usuario_id=gestor["id"], acao="VER_AUDITORIA")
    assert len(regs) >= 1


def test_nao_ha_como_alterar_ou_apagar_pela_api(client, gestor):
    for metodo in ("post", "put", "patch", "delete"):
        r = getattr(client, metodo)("/auditoria/acessos", headers=gestor["cab"])
        assert r.status_code == 405


def test_paginacao_sem_repetir_nem_pular(client, gestor, medico, atendimento_id):
    for _ in range(5):
        client.get(f"/atendimentos/{atendimento_id}", headers=medico["cab"])
    total = _acessos(client, gestor, usuario_id=medico["id"], limit=200)
    vistos, offset = [], 0
    while True:
        pag = _acessos(client, gestor, usuario_id=medico["id"], limit=3, offset=offset)
        vistos += [x["id"] for x in pag]
        if len(pag) < 3:
            break
        offset += 3
    assert len(vistos) == len(set(vistos)) == len(total)


def test_filtros_invalidos_retornam_422(client, gestor):
    assert client.get("/auditoria/acessos", headers=gestor["cab"], params={"limit": 0}).status_code == 422
    assert client.get("/auditoria/acessos", headers=gestor["cab"], params={"usuario_id": "nao-e-uuid"}).status_code == 422
