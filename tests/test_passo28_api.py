"""Testes do passo 28 que PRECISAM do banco (login bloqueado e foto sem GPS no servidor).

    docker compose exec api pytest tests/test_passo28_api.py -v
"""
import struct
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.limitador import limitador_login, max_falhas
from app.core.security import hash_senha
from app.main import app
from app.models import Atendimento, Paciente, Perfil, Usuario
from app.services import midia_service

DOMINIO = "@passo28.medgraph"
SENHA = "senha12345"


def _seg(marcador, corpo):
    return b"\xff" + bytes([marcador]) + struct.pack(">H", len(corpo) + 2) + corpo


JPEG_COM_GPS = (b"\xff\xd8" + _seg(0xE1, b"Exif\x00\x00II*\x00\x08\x00\x00\x00GPSLatitude-3.119")
                + _seg(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\x12\x34\x56" + b"\xff\xd9")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    with SessionLocal() as db:
        ids = select(Usuario.id).where(Usuario.email.like(f"%{DOMINIO}"))
        db.execute(delete(Atendimento).where(Atendimento.agente_id.in_(ids)))
        db.execute(delete(Paciente).where(Paciente.nome.like("Paciente P28%")))
        db.execute(delete(Usuario).where(Usuario.email.like(f"%{DOMINIO}")))
        db.commit()


@pytest.fixture(scope="module")
def pasta_temporaria(tmp_path_factory):
    antigo = settings.UPLOAD_DIR
    settings.UPLOAD_DIR = str(tmp_path_factory.mktemp("uploads"))
    yield
    settings.UPLOAD_DIR = antigo


def _criar_usuario(perfil):
    email = f"{perfil.value.lower()}{uuid.uuid4().hex[:8]}{DOMINIO}"
    with SessionLocal() as db:
        db.add(Usuario(nome=f"{perfil.value} P28", email=email, senha_hash=hash_senha(SENHA), perfil=perfil))
        db.commit()
    return email


def _entrar(client, email, senha=SENHA):
    return client.post("/auth/login", data={"username": email, "password": senha})


def test_login_e_bloqueado_depois_de_varias_senhas_erradas(client):
    email = _criar_usuario(Perfil.MEDICO)
    for _ in range(max_falhas()):
        assert _entrar(client, email, "senhaerrada1").status_code == 401
    r = _entrar(client, email)  # mesmo com a senha certa, fica bloqueado por um tempo
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0
    assert "tentativas" in r.json()["detail"].lower()
    limitador_login.zerar()
    assert _entrar(client, email).status_code == 200


def test_login_certo_nao_acumula_falhas(client):
    email = _criar_usuario(Perfil.MEDICO)
    for _ in range(max_falhas() - 1):
        assert _entrar(client, email, "senhaerrada1").status_code == 401
    assert _entrar(client, email).status_code == 200
    for _ in range(max_falhas() - 1):
        assert _entrar(client, email, "senhaerrada1").status_code == 401
    assert _entrar(client, email).status_code == 200


def test_foto_enviada_fica_sem_gps_no_servidor(client, pasta_temporaria):
    email_acs = _criar_usuario(Perfil.ACS)
    cab = {"Authorization": f"Bearer {_entrar(client, email_acs).json()['access_token']}"}
    r = client.post("/atendimentos", headers=cab, json={
        "paciente": {"nome": f"Paciente P28 {uuid.uuid4().hex[:6]}"}, "data_hora": "2026-10-06T09:00:00-04:00",
        "latitude": -3.119, "longitude": -60.021, "municipio": "Manaus"})
    assert r.status_code == 201
    aid = r.json()["id"]
    r = client.put(f"/atendimentos/{aid}/midia/foto", headers=cab, files={"arquivo": ("f.jpg", JPEG_COM_GPS, "image/jpeg")})
    assert r.status_code == 200
    gravado = midia_service.resolver(r.json()["caminho"]).read_bytes()
    assert b"GPSLatitude" not in gravado and b"Exif" not in gravado
    assert gravado.endswith(b"\xff\xd9")


def test_health_nao_mostra_detalhes_de_erro(client):
    corpo = client.get("/health").json()
    assert not [k for k in corpo if k.endswith("_detalhe")]
