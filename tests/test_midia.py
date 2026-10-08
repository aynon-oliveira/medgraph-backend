"""Testes do upload de áudio e foto (passo 12).

Os testes de detecção de formato não precisam de banco. Os de endpoint precisam. Execute dentro do container:
    docker compose exec api pytest -v
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_senha
from app.main import app
from app.models import Atendimento, Paciente, Perfil, Usuario
from app.services import midia_service

DOMINIO = "@midia.medgraph"
SENHA = "senha12345"

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 64
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64
WAV = b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 64
M4A = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 64
EXECUTAVEL = b"MZ\x90\x00" + b"\x00" * 64


# ---------- detecção pelo conteúdo (sem banco) ----------
def test_detecta_fotos_pelo_conteudo():
    assert midia_service.detectar_tipo("foto", JPEG)[1] == "image/jpeg"
    assert midia_service.detectar_tipo("foto", PNG)[1] == "image/png"
    assert midia_service.detectar_tipo("foto", WEBP)[1] == "image/webp"


def test_detecta_audios_pelo_conteudo():
    assert midia_service.detectar_tipo("audio", WEBM)[1] == "audio/webm"
    assert midia_service.detectar_tipo("audio", WAV)[1] == "audio/wav"
    assert midia_service.detectar_tipo("audio", M4A)[1] == "audio/mp4"


def test_recusa_executavel_e_troca_de_categoria():
    assert midia_service.detectar_tipo("foto", EXECUTAVEL) is None
    assert midia_service.detectar_tipo("audio", EXECUTAVEL) is None
    assert midia_service.detectar_tipo("foto", WEBM) is None  # áudio não passa como foto
    assert midia_service.detectar_tipo("audio", JPEG) is None  # foto não passa como áudio


def test_resolver_nao_sai_da_pasta_de_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "up"))
    fora = tmp_path / "segredo.txt"
    fora.write_text("não pode vazar")
    assert midia_service.resolver("../segredo.txt") is None
    assert midia_service.resolver(str(fora)) is None
    assert midia_service.resolver(None) is None


# ---------- endpoints (com banco) ----------
def _email(prefixo):
    return f"{prefixo}{uuid.uuid4().hex[:8]}{DOMINIO}"


def _login(client, email):
    r = client.post("/auth/login", data={"username": email, "password": SENHA})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _arquivo(conteudo, nome="x.bin", tipo="application/octet-stream"):
    return {"arquivo": (nome, conteudo, tipo)}


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
        db.execute(delete(Atendimento).where(Atendimento.agente_id.in_(ids)))
        db.execute(delete(Paciente).where(Paciente.nome.like("Paciente Midia%")))
        db.execute(delete(Usuario).where(Usuario.email.like(f"%{DOMINIO}")))
        db.commit()


@pytest.fixture(scope="module")
def gestor(client):
    email = _email("gestor")
    with SessionLocal() as db:
        db.add(Usuario(nome="Gestor Midia", email=email, senha_hash=hash_senha(SENHA), perfil=Perfil.GESTOR))
        db.commit()
    return _login(client, email)


def _novo_usuario(client, gestor, perfil):
    email = _email(perfil.lower())
    r = client.post(
        "/auth/registrar",
        headers=gestor,
        json={"nome": f"{perfil} Midia", "email": email, "senha": SENHA, "perfil": perfil},
    )
    assert r.status_code == 201
    return _login(client, email)


@pytest.fixture(scope="module")
def acs1(client, gestor):
    return _novo_usuario(client, gestor, "ACS")


@pytest.fixture(scope="module")
def acs2(client, gestor):
    return _novo_usuario(client, gestor, "ACS")


@pytest.fixture(scope="module")
def medico(client, gestor):
    return _novo_usuario(client, gestor, "MEDICO")


@pytest.fixture
def atendimento_id(client, acs1):
    r = client.post(
        "/atendimentos",
        headers=acs1,
        json={
            "paciente": {"nome": f"Paciente Midia {uuid.uuid4().hex[:6]}"},
            "data_hora": "2026-10-06T09:00:00-04:00",
            "latitude": -3.119,
            "longitude": -60.021,
            "municipio": "Manaus",
        },
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_sem_token_retorna_401(client, atendimento_id):
    assert client.put(f"/atendimentos/{atendimento_id}/midia/foto", files=_arquivo(JPEG)).status_code == 401
    assert client.get(f"/atendimentos/{atendimento_id}/midia/foto").status_code == 401


def test_acs_envia_foto_e_medico_baixa(client, acs1, medico, atendimento_id):
    r = client.put(f"/atendimentos/{atendimento_id}/midia/foto", headers=acs1, files=_arquivo(JPEG, "a.jpg"))
    assert r.status_code == 200
    assert r.json()["caminho"].endswith(".jpg")

    baixado = client.get(f"/atendimentos/{atendimento_id}/midia/foto", headers=medico)
    assert baixado.status_code == 200
    assert baixado.headers["content-type"] == "image/jpeg"
    assert baixado.content == JPEG
    assert baixado.headers["cache-control"] == "private, no-store"


def test_atendimento_passa_a_apontar_para_o_arquivo(client, acs1, atendimento_id):
    client.put(f"/atendimentos/{atendimento_id}/midia/audio", headers=acs1, files=_arquivo(WEBM, "voz.webm"))
    corpo = client.get(f"/atendimentos/{atendimento_id}", headers=acs1).json()
    assert corpo["relato_voz_path"].startswith("audio/")
    assert corpo["imagem_exantema_path"] is None


def test_substituir_apaga_o_arquivo_antigo(client, acs1, atendimento_id):
    primeiro = client.put(f"/atendimentos/{atendimento_id}/midia/foto", headers=acs1, files=_arquivo(JPEG)).json()
    segundo = client.put(f"/atendimentos/{atendimento_id}/midia/foto", headers=acs1, files=_arquivo(PNG)).json()
    assert primeiro["caminho"] != segundo["caminho"]
    assert midia_service.resolver(primeiro["caminho"]) is None
    assert midia_service.resolver(segundo["caminho"]) is not None


def test_nome_enviado_pelo_cliente_e_ignorado(client, acs1, atendimento_id):
    r = client.put(
        f"/atendimentos/{atendimento_id}/midia/foto", headers=acs1, files=_arquivo(JPEG, "../../etc/passwd.jpg")
    )
    assert r.status_code == 200
    assert ".." not in r.json()["caminho"] and "passwd" not in r.json()["caminho"]


def test_formato_invalido_retorna_415(client, acs1, atendimento_id):
    r = client.put(
        f"/atendimentos/{atendimento_id}/midia/foto",
        headers=acs1,
        files=_arquivo(EXECUTAVEL, "virus.jpg", "image/jpeg"),  # mente no nome e no tipo
    )
    assert r.status_code == 415


def test_arquivo_vazio_retorna_422(client, acs1, atendimento_id):
    assert client.put(f"/atendimentos/{atendimento_id}/midia/foto", headers=acs1, files=_arquivo(b"")).status_code == 422


def test_arquivo_grande_demais_retorna_413_e_nao_deixa_resto(client, acs1, atendimento_id, monkeypatch):
    monkeypatch.setattr(settings, "MAX_FOTO_MB", 1)
    grande = JPEG + b"\x00" * (1024 * 1024 + 10)
    r = client.put(f"/atendimentos/{atendimento_id}/midia/foto", headers=acs1, files=_arquivo(grande))
    assert r.status_code == 413
    pasta = midia_service.pasta_base() / "foto"
    assert not list(pasta.glob(f"{atendimento_id}-*"))  # o arquivo pela metade foi apagado


def test_outro_acs_nao_ve_nem_envia(client, acs2, atendimento_id):
    assert client.put(f"/atendimentos/{atendimento_id}/midia/foto", headers=acs2, files=_arquivo(JPEG)).status_code == 404
    assert client.get(f"/atendimentos/{atendimento_id}/midia/foto", headers=acs2).status_code == 404


def test_medico_nao_envia_e_gestor_nao_baixa(client, acs1, medico, gestor, atendimento_id):
    client.put(f"/atendimentos/{atendimento_id}/midia/foto", headers=acs1, files=_arquivo(JPEG))
    assert client.put(f"/atendimentos/{atendimento_id}/midia/foto", headers=medico, files=_arquivo(JPEG)).status_code == 403
    assert client.get(f"/atendimentos/{atendimento_id}/midia/foto", headers=gestor).status_code == 403


def test_baixar_sem_arquivo_retorna_404(client, acs1, atendimento_id):
    assert client.get(f"/atendimentos/{atendimento_id}/midia/audio", headers=acs1).status_code == 404


def test_caminho_malicioso_no_banco_nao_vaza_arquivo(client, acs1, atendimento_id, tmp_path):
    segredo = tmp_path / "segredo.txt"
    segredo.write_text("nao pode vazar")
    with SessionLocal() as db:
        a = db.get(Atendimento, uuid.UUID(atendimento_id))
        a.imagem_exantema_path = "../../../../../../" + str(segredo).lstrip("/")
        db.commit()
    assert client.get(f"/atendimentos/{atendimento_id}/midia/foto", headers=acs1).status_code == 404


def test_categoria_desconhecida_retorna_422(client, acs1, atendimento_id):
    assert client.put(f"/atendimentos/{atendimento_id}/midia/video", headers=acs1, files=_arquivo(JPEG)).status_code == 422
