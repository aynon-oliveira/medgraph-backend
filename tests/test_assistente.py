"""Testes do assistente de IA (passo 24). O Gemini de verdade NUNCA é chamado: a função é trocada por uma falsa.
Execute dentro do container:  docker compose exec api pytest tests/test_assistente.py -v
"""
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_senha
from app.main import app
from app.models import Atendimento, Paciente, Perfil, RegistroAcesso, Usuario
from app.services import gemini_service

DOMINIO = "@assistente.medgraph"
SENHA = "senha12345"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture(autouse=True)
def gemini_ligado(monkeypatch):
    monkeypatch.setattr(gemini_service, "_dormir", lambda s: None)   # os testes não esperam de verdade
    monkeypatch.setenv("GEMINI_CACHE_SEG", "0")                       # sem cache, salvo no teste do cache
    monkeypatch.setenv("GEMINI_API_KEY", "chave-de-teste")
    monkeypatch.setenv("GEMINI_ATIVO", "1")
    monkeypatch.delenv("GEMINI_LIMITE_HORA", raising=False)
    gemini_service.zerar_limites()
    yield
    gemini_service.zerar_limites()


# ---------- serviço (sem banco) ----------
def test_limpar_remove_documentos_telefone_e_email():
    t = gemini_service.limpar("CPF 123.456.789-09, tel (92) 99123-4567, mail ana@exemplo.com, febre há 3 dias")
    assert "123.456.789-09" not in t and "99123-4567" not in t and "ana@exemplo.com" not in t
    assert "febre há 3 dias" in t


def test_limpar_corta_no_limite():
    assert len(gemini_service.limpar("a " * 5000, limite=100)) <= 100


def test_ficha_nao_leva_nome_nem_endereco():
    t = gemini_service.preparar_ficha(idade=34, sexo="F", sintomas=["febre"], relato="febre", transcricao=None,
                                      nivel="ALTO", score=0.8, sinais_alarme=["vômitos persistentes"], recomendacao="Encaminhar")
    assert "Idade: 34" in t and "feminino" in t and "vômitos persistentes" in t and "ALTO" in t
    assert t.startswith("<dados>") and t.endswith("</dados>")


def test_disponibilidade_depende_da_chave_e_do_ativo(monkeypatch):
    assert gemini_service.disponivel() is True
    monkeypatch.setenv("GEMINI_ATIVO", "0")
    assert gemini_service.disponivel() is False
    monkeypatch.setenv("GEMINI_ATIVO", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    assert gemini_service.disponivel() is False
    with pytest.raises(gemini_service.IAIndisponivel):
        gemini_service.gerar("x", "y")


def test_extrair_texto_e_resposta_vazia():
    ok = {"candidates": [{"content": {"parts": [{"text": "Resumo: "}, {"text": "ok"}]}}]}
    assert gemini_service.extrair_texto(ok) == "Resumo: ok"
    for ruim in ({}, {"candidates": []}, {"candidates": [{"finishReason": "SAFETY"}]}, {"candidates": [{"content": {"parts": [{"text": " "}]}}]}):
        with pytest.raises(gemini_service.IAFalhou):
            gemini_service.extrair_texto(ruim)


class _Resp:
    def __init__(self, codigo, corpo=None):
        self.status_code, self._c = codigo, corpo

    def json(self):
        if self._c is None:
            raise ValueError("sem json")
        return self._c


def test_envio_manda_chave_no_cabecalho_e_traduz_erros(monkeypatch):
    visto = {}

    def falso(url, json=None, headers=None, timeout=None):
        visto.update(url=url, headers=headers, json=json)
        return _Resp(200, {"candidates": [{"content": {"parts": [{"text": "feito"}]}}]})
    monkeypatch.setattr(httpx, "post", falso)
    assert gemini_service.gerar("regras", "texto", imagem=JPEG) == "feito"
    assert visto["headers"]["x-goog-api-key"] == "chave-de-teste"
    assert "chave-de-teste" not in visto["url"]  # a chave nunca vai na URL
    partes = visto["json"]["contents"][0]["parts"]
    assert partes[1]["inline_data"]["mime_type"] == "image/jpeg"

    for codigo, erro in ((401, gemini_service.IAIndisponivel), (403, gemini_service.IAIndisponivel),
                         (429, gemini_service.IALimite), (500, gemini_service.IAFalhou)):
        monkeypatch.setattr(httpx, "post", lambda *a, _c=codigo, **k: _Resp(_c, {}))
        with pytest.raises(erro):
            gemini_service.gerar("r", "t")

    def sem_rede(*a, **k):
        raise httpx.ConnectError("sem internet")
    monkeypatch.setattr(httpx, "post", sem_rede)
    with pytest.raises(gemini_service.IAIndisponivel):
        gemini_service.gerar("r", "t")


def test_erro_temporario_tenta_de_novo_e_depois_usa_o_reserva(monkeypatch):
    chamadas = []
    ok = {"candidates": [{"content": {"parts": [{"text": "feito"}]}}]}

    def falso(url, json=None, headers=None, timeout=None):
        chamadas.append(url)
        return _Resp(200, ok) if len(chamadas) == 2 else _Resp(503, {"error": {"message": "overloaded"}})
    monkeypatch.setattr(httpx, "post", falso)
    assert gemini_service.gerar("r", "t") == "feito"      # 503 e, na segunda tentativa, deu certo
    assert len(chamadas) == 2 and chamadas[0] == chamadas[1]

    chamadas.clear()
    monkeypatch.setenv("GEMINI_MODELO", "principal")
    monkeypatch.setenv("GEMINI_MODELO_RESERVA", "reserva")

    def so_reserva(url, json=None, headers=None, timeout=None):
        chamadas.append(url)
        return _Resp(200, ok) if "reserva" in url else _Resp(503, {})
    monkeypatch.setattr(httpx, "post", so_reserva)
    assert gemini_service.gerar("r", "t") == "feito"
    assert "principal" in chamadas[0] and "reserva" in chamadas[-1]

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(503, {}))   # tudo fora do ar
    with pytest.raises(gemini_service.IAFalhou):
        gemini_service.gerar("r", "t")


def test_cota_do_google_esgotada_usa_o_reserva(monkeypatch):
    ok = {"candidates": [{"content": {"parts": [{"text": "feito"}]}}]}
    monkeypatch.setenv("GEMINI_MODELO", "principal")
    monkeypatch.setenv("GEMINI_MODELO_RESERVA", "reserva")
    chamadas = []

    def falso(url, json=None, headers=None, timeout=None):
        chamadas.append(url)
        return _Resp(200, ok) if "reserva" in url else _Resp(429, {"error": {"message": "quota"}})
    monkeypatch.setattr(httpx, "post", falso)
    assert gemini_service.gerar("r", "t") == "feito"
    assert len(chamadas) == 2                       # 429 não repete no mesmo modelo
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(429, {}))
    with pytest.raises(gemini_service.IALimite):    # as duas cotas esgotadas
        gemini_service.gerar("r", "t")


def test_resposta_repetida_vem_do_cache_e_nao_chama_o_google(monkeypatch):
    monkeypatch.setenv("GEMINI_CACHE_SEG", "600")
    n = []
    ok = {"candidates": [{"content": {"parts": [{"text": "feito"}]}}]}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (n.append(1) or _Resp(200, ok)))
    assert gemini_service.gerar("r", "mesma ficha") == "feito"
    assert gemini_service.gerar("r", "mesma ficha") == "feito"
    assert len(n) == 1                                    # a segunda veio da memória
    assert gemini_service.gerar("r", "outra ficha") == "feito" and len(n) == 2


def test_envia_pedido_curto_sem_pensar_e_recua_se_o_modelo_recusar(monkeypatch):
    vistos = []
    ok = {"candidates": [{"content": {"parts": [{"text": "feito"}]}}]}

    def falso(url, json=None, headers=None, timeout=None):
        vistos.append(dict(json["generationConfig"]))
        return _Resp(400, {"error": {"message": "thinking"}}) if "thinkingConfig" in json["generationConfig"] else _Resp(200, ok)
    monkeypatch.setattr(httpx, "post", falso)
    assert gemini_service.gerar("r", "t") == "feito"
    assert vistos[0]["maxOutputTokens"] <= 1000 and "thinkingConfig" in vistos[0]
    assert "thinkingConfig" not in vistos[-1]             # repetiu sem o parâmetro e deu certo


def test_limite_por_hora(monkeypatch):
    monkeypatch.setenv("GEMINI_LIMITE_HORA", "2")
    u = uuid.uuid4()
    gemini_service.conferir_limite(u)
    gemini_service.conferir_limite(u)
    with pytest.raises(gemini_service.IALimite):
        gemini_service.conferir_limite(u)
    gemini_service.conferir_limite(uuid.uuid4())  # outro usuário não é afetado


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
        db.execute(delete(Paciente).where(Paciente.nome.like("Paciente Assist%")))
        db.execute(delete(Usuario).where(Usuario.email.like(f"%{DOMINIO}")))
        db.commit()


@pytest.fixture(scope="module")
def gestor(client):
    email = _email("gestor")
    with SessionLocal() as db:
        db.add(Usuario(nome="Gestor Assist", email=email, senha_hash=hash_senha(SENHA), perfil=Perfil.GESTOR))
        db.commit()
    return _login(client, email)


def _novo(client, gestor, perfil):
    email = _email(perfil.lower())
    r = client.post("/auth/registrar", headers=gestor,
                    json={"nome": f"{perfil} Assist", "email": email, "senha": SENHA, "perfil": perfil})
    assert r.status_code == 201
    return _login(client, email)


@pytest.fixture(scope="module")
def acs1(client, gestor):
    return _novo(client, gestor, "ACS")


@pytest.fixture(scope="module")
def medico(client, gestor):
    return _novo(client, gestor, "MEDICO")


@pytest.fixture
def atendimento_id(client, acs1):
    r = client.post("/atendimentos", headers=acs1, json={
        "paciente": {"nome": f"Paciente Assist {uuid.uuid4().hex[:6]}", "sexo": "F", "data_nascimento": "1990-05-10"},
        "data_hora": "2026-10-06T09:00:00-04:00", "latitude": -3.119, "longitude": -60.021,
        "municipio": "Manaus", "bairro": "Centro", "sintomas": ["febre", "cefaleia"],
        "relato_texto": "febre há 3 dias, me ligue 92 99123-4567",
        "nivel_risco": "ALTO",
        "resultado": {"score_probabilidade": 0.8, "sinais_alarme": ["vômitos persistentes"], "recomendacao": "Encaminhar"}})
    assert r.status_code == 201
    return r.json()["id"]


@pytest.fixture
def gerar_falso(monkeypatch):
    chamadas = []

    def falso(instrucao, texto, imagem=None, mime="image/jpeg"):
        chamadas.append({"instrucao": instrucao, "texto": texto, "imagem": imagem, "mime": mime})
        return "Resumo: texto de teste"
    monkeypatch.setattr(gemini_service, "gerar", falso)
    return chamadas


def test_status_exige_login_e_informa_disponibilidade(client, medico, monkeypatch):
    assert client.get("/api/v1/assistente/status").status_code == 401
    assert client.get("/api/v1/assistente/status", headers=medico).json()["disponivel"] is True
    monkeypatch.setenv("GEMINI_API_KEY", "")
    j = client.get("/api/v1/assistente/status", headers=medico).json()
    assert j["disponivel"] is False and j["modelo"] is None


def test_resumo_so_medico(client, acs1, gestor, atendimento_id):
    url = f"/atendimentos/{atendimento_id}/assistente/resumo"
    assert client.post(url).status_code == 401
    assert client.post(url, headers=acs1).status_code == 403
    assert client.post(url, headers=gestor).status_code == 403


def test_resumo_nao_envia_identificacao_e_nao_muda_o_risco(client, medico, atendimento_id, gerar_falso):
    r = client.post(f"/atendimentos/{atendimento_id}/assistente/resumo", headers=medico)
    assert r.status_code == 200
    j = r.json()
    assert j["resumo"] == "Resumo: texto de teste" and j["nivel_risco"] == "ALTO" and j["aviso"]
    enviado = gerar_falso[0]["texto"]
    assert "Paciente Assist" not in enviado and "Manaus" not in enviado and "Centro" not in enviado
    assert "99123-4567" not in enviado          # telefone digitado no relato foi removido
    assert "vômitos persistentes" in enviado and "ALTO" in enviado and "febre" in enviado
    with SessionLocal() as db:
        assert db.get(Atendimento, uuid.UUID(atendimento_id)).nivel_risco.value == "ALTO"


def test_uso_do_assistente_fica_na_auditoria(client, medico, gestor, atendimento_id, gerar_falso):
    client.post(f"/atendimentos/{atendimento_id}/assistente/resumo", headers=medico)
    r = client.get("/auditoria/acessos", headers=gestor, params={"acao": "USAR_ASSISTENTE_IA"})
    assert r.status_code == 200
    assert any(x["acao"] == "USAR_ASSISTENTE_IA" and str(x["recurso_id"]) == atendimento_id for x in r.json())


def test_desligado_retorna_503(client, medico, atendimento_id, monkeypatch):
    monkeypatch.setenv("GEMINI_ATIVO", "0")
    assert client.post(f"/atendimentos/{atendimento_id}/assistente/resumo", headers=medico).status_code == 503


def test_erros_do_gemini_viram_respostas_claras(client, medico, atendimento_id, monkeypatch):
    for erro, codigo in ((gemini_service.IAFalhou("x"), 502), (gemini_service.IAIndisponivel("x"), 503), (gemini_service.IALimite("x"), 429)):
        def quebra(*a, _e=erro, **k):
            raise _e
        monkeypatch.setattr(gemini_service, "gerar", quebra)
        assert client.post(f"/atendimentos/{atendimento_id}/assistente/resumo", headers=medico).status_code == codigo


def test_limite_por_hora_na_rota(client, medico, atendimento_id, gerar_falso, monkeypatch):
    monkeypatch.setenv("GEMINI_LIMITE_HORA", "1")
    url = f"/atendimentos/{atendimento_id}/assistente/resumo"
    assert client.post(url, headers=medico).status_code == 200
    assert client.post(url, headers=medico).status_code == 429


def test_atendimento_inexistente_404(client, medico, gerar_falso):
    assert client.post(f"/atendimentos/{uuid.uuid4()}/assistente/resumo", headers=medico).status_code == 404


def _com_foto(client, acs1, atendimento_id):
    r = client.put(f"/atendimentos/{atendimento_id}/midia/foto", headers=acs1, files={"arquivo": ("f.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 200


def test_foto_exige_ciencia_e_foto_existente(client, acs1, medico, atendimento_id, gerar_falso):
    url = f"/atendimentos/{atendimento_id}/assistente/foto"
    assert client.post(url, headers=medico, json={}).status_code == 422                  # sem ciente
    assert client.post(url, headers=medico, json={"ciente": True}).status_code == 404    # sem foto
    assert gerar_falso == []                                                             # nada saiu do servidor


def test_foto_vai_ao_gemini_so_com_ciencia(client, acs1, medico, gestor, atendimento_id, gerar_falso):
    _com_foto(client, acs1, atendimento_id)
    url = f"/atendimentos/{atendimento_id}/assistente/foto"
    assert client.post(url, headers=gestor, json={"ciente": True}).status_code == 403
    assert client.post(url, headers=acs1, json={"ciente": True}).status_code == 403
    r = client.post(url, headers=medico, json={"ciente": True})
    assert r.status_code == 200 and r.json()["descricao"] and r.json()["aviso"]
    assert gerar_falso[0]["imagem"] == JPEG and gerar_falso[0]["mime"] == "image/jpeg"
    assert "Paciente Assist" not in gerar_falso[0]["texto"]


def test_painel_so_gestor_e_so_numeros(client, acs1, medico, gestor, atendimento_id, gerar_falso):
    assert client.post("/api/v1/assistente/painel", headers=medico, json={}).status_code == 403
    assert client.post("/api/v1/assistente/painel", headers=acs1, json={}).status_code == 403
    r = client.post("/api/v1/assistente/painel", headers=gestor, json={"dias": 365})
    assert r.status_code == 200
    j = r.json()
    assert j["resumo"] and j["numeros"]["total"] >= 1
    enviado = gerar_falso[0]["texto"]
    assert "Atendimentos no período" in enviado and "Paciente Assist" not in enviado


def test_painel_valida_dias(client, gestor, gerar_falso):
    assert client.post("/api/v1/assistente/painel", headers=gestor, json={"dias": 0}).status_code == 422
    assert client.post("/api/v1/assistente/painel", headers=gestor, json={"dias": 9999}).status_code == 422


def test_painel_sem_atendimentos_nao_chama_o_gemini(client, gestor, gerar_falso, monkeypatch):
    from app.routers import assistente
    monkeypatch.setattr(assistente, "agregar_painel", lambda db, dias: {"dias": dias, "total": 0, "alto": 0, "moderado": 0,
                                                                     "periodo_anterior": 0, "localidades": [], "sintomas": []})
    assert client.post("/api/v1/assistente/painel", headers=gestor, json={"dias": 7}).status_code == 404
    assert gerar_falso == []


def test_modelo_que_recusa_thinking_e_lembrado(monkeypatch):
    """Depois de um 400 por thinkingConfig, as próximas chamadas já vão sem o campo (1 chamada só)."""
    from app.services import gemini_service as g
    chamadas = []

    class Resp:
        def __init__(self, c, b):
            self.status_code, self._b = c, b

        def json(self):
            return self._b

    ok = {"candidates": [{"content": {"parts": [{"text": "feito"}]}}]}

    def falso(url, json=None, headers=None, timeout=None):
        chamadas.append("thinkingConfig" in json["generationConfig"])
        return Resp(400, {"error": {"message": "x"}}) if chamadas[-1] else Resp(200, ok)

    monkeypatch.setattr(g.httpx, "post", falso)
    g.zerar_limites()
    assert g.gerar("r", "a") == "feito" and chamadas == [True, False]
    chamadas.clear()
    assert g.gerar("r", "b") == "feito" and chamadas == [False]
