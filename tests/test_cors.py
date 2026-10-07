"""Testes do CORS (passo 11): o navegador só pode chamar a API a partir de origens autorizadas.

Execute dentro do container:
    docker compose exec api pytest -v
"""
from fastapi.testclient import TestClient

from app.core.config import ORIGENS_PADRAO, Settings, settings
from app.main import app

# Sem "with": o lifespan não roda, e o preflight não precisa de banco.
client = TestClient(app)

PERMITIDA = settings.cors_origins_list[0]
NEGADA = "http://site-desconhecido.example"


def _preflight(origem, metodo="POST", cabecalhos="authorization,content-type"):
    return client.options(
        "/api/v1/sincronizar",
        headers={
            "Origin": origem,
            "Access-Control-Request-Method": metodo,
            "Access-Control-Request-Headers": cabecalhos,
        },
    )


def test_preflight_de_origem_permitida_e_aceito():
    r = _preflight(PERMITIDA)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == PERMITIDA
    assert "POST" in r.headers["access-control-allow-methods"]
    permitidos = r.headers["access-control-allow-headers"].lower()
    assert "authorization" in permitidos and "content-type" in permitidos


def test_preflight_de_origem_desconhecida_e_recusado():
    r = _preflight(NEGADA)
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers


def test_cabecalho_nao_autorizado_no_preflight_e_recusado():
    r = _preflight(PERMITIDA, cabecalhos="x-cabecalho-estranho")
    assert r.status_code == 400


def test_resposta_normal_traz_o_cabecalho_cors_para_origem_permitida():
    r = client.get("/health", headers={"Origin": PERMITIDA})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == PERMITIDA


def test_resposta_normal_nao_traz_cabecalho_cors_para_origem_desconhecida():
    r = client.get("/health", headers={"Origin": NEGADA})
    assert "access-control-allow-origin" not in r.headers


def test_nao_libera_cookies_nem_curinga():
    r = _preflight(PERMITIDA)
    assert "access-control-allow-credentials" not in r.headers
    assert r.headers["access-control-allow-origin"] != "*"


def test_lista_de_origens_vem_do_ambiente(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", " http://painel.exemplo.org/ , http://localhost:9000 ")
    assert Settings().cors_origins_list == ["http://painel.exemplo.org", "http://localhost:9000"]


def test_padrao_cobre_as_portas_de_desenvolvimento():
    assert "http://localhost:5500" in ORIGENS_PADRAO.split(",")
