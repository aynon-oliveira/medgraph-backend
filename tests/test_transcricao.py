"""Testes da transcrição (passo 22). O Whisper de verdade NÃO roda aqui: a função é trocada por uma falsa.
Execute dentro do container:  docker compose exec api pytest tests/test_transcricao.py -v
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_senha
from app.main import app
from app.models import Atendimento, Paciente, Perfil, RegistroAcesso, Usuario
from app.services import transcricao_service

DOMINIO = "@transcricao.medgraph"
SENHA = "senha12345"
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64


# ---------- serviço (sem banco) ----------
def test_juntar_trechos_limpa_espacos():
    class T:
        def __init__(self, t): self.text = t
    assert transcricao_service.juntar_trechos([T(" estou com febre "), T(""), T("  e dor no corpo")]) == "estou com febre e dor no corpo"
    assert transcricao_service.juntar_trechos([]) == ""


def test_desligado_no_env_fica_indisponivel(monkeypatch):
    monkeypatch.setenv("WHISPER_ATIVO", "0")
    assert transcricao_service.disponivel() is False
    with pytest.raises(transcricao_service.TranscricaoIndisponivel):
        transcricao_service.transcrever("qualquer.webm")


def test_nome_do_modelo_padrao_e_do_env(monkeypatch):
    monkeypatch.delenv("WHISPER_MODELO", raising=False)
    assert transcricao_service.nome_do_modelo() == "base"
    monkeypatch.setenv("WHISPER_MODELO", "small")
    assert transcricao_service.nome_do_modelo() == "small"


# ---------- rotas (com banco) ----------
def _email(p):
    return f"{p}{uuid.uuid4().hex[:8]}{DOMINIO}"


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
        db.execute(delete(Atendimento).where(Atendimento.agente_id.in_(ids)))
        db.execute(delete(Paciente).where(Paciente.nome.like("Paciente Transc%")))
        db.execute(delete(Usuario).where(Usuario.email.like(f"%{DOMINIO}")))
        db.commit()


@pytest.fixture(scope="module")
def gestor(client):
    email = _email("gestor")
    with SessionLocal() as db:
        db.add(Usuario(nome="Gestor Transc", email=email, senha_hash=hash_senha(SENHA), perfil=Perfil.GESTOR))
        db.commit()
    return _login(client, email)


def _novo(client, gestor, perfil):
    email = _email(perfil.lower())
    r = client.post("/auth/registrar", headers=gestor,
                    json={"nome": f"{perfil} Transc", "email": email, "senha": SENHA, "perfil": perfil})
    assert r.status_code == 201
    return _login(client, email)


@pytest.fixture(scope="module")
def acs1(client, gestor):
    return _novo(client, gestor, "ACS")


@pytest.fixture(scope="module")
def acs2(client, gestor):
    return _novo(client, gestor, "ACS")


@pytest.fixture(scope="module")
def medico(client, gestor):
    return _novo(client, gestor, "MEDICO")


@pytest.fixture
def atendimento_id(client, acs1):
    r = client.post("/atendimentos", headers=acs1, json={
        "paciente": {"nome": f"Paciente Transc {uuid.uuid4().hex[:6]}"},
        "data_hora": "2026-10-06T09:00:00-04:00", "latitude": -3.119, "longitude": -60.021, "municipio": "Manaus"})
    assert r.status_code == 201
    return r.json()["id"]


def _com_audio(client, acs1, atendimento_id):
    r = client.put(f"/atendimentos/{atendimento_id}/midia/audio", headers=acs1, files={"arquivo": ("v.webm", WEBM, "audio/webm")})
    assert r.status_code == 200


def test_exige_login(client, atendimento_id):
    assert client.post(f"/atendimentos/{atendimento_id}/transcricao").status_code == 401


def test_gestor_nao_transcreve(client, gestor, atendimento_id):
    assert client.post(f"/atendimentos/{atendimento_id}/transcricao", headers=gestor).status_code == 403


def test_sem_audio_retorna_404(client, acs1, atendimento_id):
    assert client.post(f"/atendimentos/{atendimento_id}/transcricao", headers=acs1).status_code == 404


def test_sem_whisper_retorna_503(client, acs1, atendimento_id, monkeypatch):
    _com_audio(client, acs1, atendimento_id)
    monkeypatch.setattr(transcricao_service, "disponivel", lambda: False)
    r = client.post(f"/atendimentos/{atendimento_id}/transcricao", headers=acs1)
    assert r.status_code == 503


def test_transcreve_guarda_e_nao_mexe_no_relato(client, acs1, medico, atendimento_id, monkeypatch):
    _com_audio(client, acs1, atendimento_id)
    monkeypatch.setattr(transcricao_service, "disponivel", lambda: True)
    monkeypatch.setattr(transcricao_service, "transcrever",
                        lambda caminho: {"texto": "febre e dor atrás dos olhos", "idioma": "pt", "modelo": "whisper-teste"})
    r = client.post(f"/atendimentos/{atendimento_id}/transcricao", headers=acs1)
    assert r.status_code == 200
    assert r.json()["transcricao"] == "febre e dor atrás dos olhos"
    with SessionLocal() as db:
        guardado = db.execute(text("SELECT transcricao_audio, relato_texto FROM atendimentos WHERE id=:i"), {"i": atendimento_id}).one()
    assert guardado[0] == "febre e dor atrás dos olhos"
    assert guardado[1] is None  # o relato digitado/original não é alterado
    # o médico lê o que ficou guardado, e isso entra na auditoria
    g = client.get(f"/atendimentos/{atendimento_id}/transcricao", headers=medico)
    assert g.status_code == 200 and g.json()["transcricao"] == "febre e dor atrás dos olhos"


def test_falha_do_whisper_vira_422(client, acs1, atendimento_id, monkeypatch):
    _com_audio(client, acs1, atendimento_id)
    monkeypatch.setattr(transcricao_service, "disponivel", lambda: True)

    def quebra(caminho):
        raise transcricao_service.TranscricaoFalhou("arquivo corrompido")
    monkeypatch.setattr(transcricao_service, "transcrever", quebra)
    assert client.post(f"/atendimentos/{atendimento_id}/transcricao", headers=acs1).status_code == 422


def test_acs_nao_transcreve_atendimento_de_outro(client, acs1, acs2, atendimento_id):
    _com_audio(client, acs1, atendimento_id)
    assert client.post(f"/atendimentos/{atendimento_id}/transcricao", headers=acs2).status_code == 404


def test_leitura_sem_transcricao_retorna_404(client, acs1, atendimento_id):
    assert client.get(f"/atendimentos/{atendimento_id}/transcricao", headers=acs1).status_code == 404
