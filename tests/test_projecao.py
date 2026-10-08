"""Testes da projeção do avanço de surtos (passo 16, RF10).

Os testes do cálculo não precisam de banco; os do endpoint precisam. Execute no container:
    docker compose exec api pytest tests/test_projecao.py -v
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, or_, select

from app.core.database import SessionLocal
from app.core.neo4j_client import fechar_driver, neo4j_session
from app.core.security import hash_senha
from app.main import app
from app.models import Atendimento, Paciente, Perfil, Usuario
from app.services import surto_service as s

DOMINIO = "@proj.medgraph"
SENHA = "SenhaInicial77"
SUF = uuid.uuid4().hex[:8]
MUN = f"TesteProj{SUF}"


# ---------- cálculo (sem banco) ----------
def test_regressao_linear_reta_exata():
    a, b = s.regressao_linear([1, 3, 5, 7])
    assert a == pytest.approx(1.0) and b == pytest.approx(2.0)


def test_regressao_linear_casos_pequenos():
    assert s.regressao_linear([]) == (0.0, 0.0)
    assert s.regressao_linear([4]) == (4.0, 0.0)
    assert s.regressao_linear([3, 3, 3]) == (pytest.approx(3.0), pytest.approx(0.0))


def test_tendencias():
    assert s.classificar_tendencia([1, 2, 4, 6], 1.7, 13, 5) == "CRESCENTE"
    assert s.classificar_tendencia([6, 4, 2, 1], -1.7, 13, 5) == "DECRESCENTE"
    assert s.classificar_tendencia([3, 3, 3, 3], 0.0, 12, 5) == "ESTAVEL"
    assert s.classificar_tendencia([0, 0, 1, 1], 0.4, 2, 5) == "DADOS_INSUFICIENTES"  # poucos casos


def test_projecao_cresce_nao_fica_negativa_e_respeita_o_teto():
    assert s.projetar_serie([1, 3, 5, 7], 2) == [9.0, 11.0]
    assert s.projetar_serie([9, 6, 3, 0], 3) == [0.0, 0.0, 0.0]  # queda não vira número negativo
    teto = s.TETO_PROJECAO * 10
    assert max(s.projetar_serie([0, 0, 0, 10], 4)) <= teto  # crescimento brusco é limitado


def test_distancia_de_um_grau_de_latitude():
    assert s.distancia_km(-3.0, -60.0, -4.0, -60.0) == pytest.approx(111.2, abs=0.3)
    assert s.distancia_km(-3.1, -60.0, -3.1, -60.0) == 0.0


def test_semanas_terminam_em_ate():
    ate = datetime(2026, 10, 7, tzinfo=timezone.utc)
    inicios = s.inicio_das_semanas(ate, 4)
    assert len(inicios) == 4
    assert inicios[-1] == ate - timedelta(days=7) and inicios[0] == ate - timedelta(days=28)


def _loc(nome, serie, lat, lon):
    return {"nome": nome, "serie": serie, "alto": 0, "lat": lat, "lon": lon, "focos": 0}


def test_vizinha_proxima_de_area_em_crescimento_fica_em_risco_e_a_distante_nao():
    locs = {
        "a": _loc("A", [1, 2, 4, 6], -3.100, -60.0),
        "b": _loc("B", [1, 1, 1, 1], -3.105, -60.0),  # ~0,55 km de A
        "c": _loc("C", [2, 2, 2, 2], -3.200, -60.0),  # ~11 km de A
    }
    r = {x["chave"]: x for x in s.prever_avanco(locs, semanas=4, horizonte=2, minimo_casos=3, raio_km=3.0)}
    assert r["a"]["tendencia"] == "CRESCENTE" and r["a"]["risco_de_expansao"] == "NENHUM"  # já é o foco do avanço
    assert r["b"]["tendencia"] == "ESTAVEL" and r["b"]["risco_de_expansao"] in ("MEDIO", "ALTO")
    assert r["b"]["vizinhas_em_crescimento"][0]["localidade"] == "A"
    assert r["c"]["risco_de_expansao"] == "NENHUM" and r["c"]["vizinhas_em_crescimento"] == []


def test_dados_insuficientes_nao_projetam_nem_espalham():
    locs = {
        "a": _loc("A", [0, 0, 0, 3], -3.1, -60.0),  # sobe, mas tem só 3 casos (mínimo 5)
        "b": _loc("B", [2, 2, 2, 2], -3.101, -60.0),
    }
    r = {x["chave"]: x for x in s.prever_avanco(locs, semanas=4, horizonte=2, minimo_casos=5, raio_km=3.0)}
    assert r["a"]["tendencia"] == "DADOS_INSUFICIENTES" and r["a"]["casos_previstos"] == []
    assert r["b"]["risco_de_expansao"] == "NENHUM"


def test_ordem_do_resultado_e_estavel():
    locs = {k: _loc(k, [2, 2, 2, 2], -3.1, -60.0 - i) for i, k in enumerate("zyx")}
    a = [x["chave"] for x in s.prever_avanco(locs, semanas=4, horizonte=1, minimo_casos=1, raio_km=1)]
    assert a == ["x", "y", "z"]  # empate total: desempata pela chave


# ---------- endpoint (com banco) ----------
def _login(client, email):
    r = client.post("/auth/login", data={"username": email, "password": SENHA})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    try:
        with SessionLocal() as db:
            pacientes = [str(i) for i in db.scalars(select(Paciente.id).where(Paciente.nome.like("Paciente Proj%")))]
            ids = select(Usuario.id).where(Usuario.email.like(f"%{DOMINIO}"))
            db.execute(delete(Atendimento).where(or_(Atendimento.agente_id.in_(ids), Atendimento.medico_id.in_(ids))))
            db.execute(delete(Paciente).where(Paciente.nome.like("Paciente Proj%")))
            db.execute(delete(Usuario).where(Usuario.email.like(f"%{DOMINIO}")))
            db.commit()
        try:
            with neo4j_session() as sessao:
                sessao.run("MATCH (p:NoPaciente) WHERE p.pacienteId IN $ids DETACH DELETE p", ids=pacientes).consume()
                sessao.run("MATCH (l:NoLocalidade) WHERE l.chave STARTS WITH 'testeproj' DETACH DELETE l").consume()
        except Exception:  # noqa: BLE001  (grafo fora do ar não pode quebrar a limpeza)
            pass
    finally:
        fechar_driver()


@pytest.fixture(scope="module")
def gestor(client):
    email = f"gestor{uuid.uuid4().hex[:8]}{DOMINIO}"
    with SessionLocal() as db:
        db.add(Usuario(nome="Gestor Proj", email=email, senha_hash=hash_senha(SENHA), perfil=Perfil.GESTOR))
        db.commit()
    return _login(client, email)


@pytest.fixture(scope="module")
def acs(client, gestor):
    email = f"acs{uuid.uuid4().hex[:8]}{DOMINIO}"
    r = client.post(
        "/auth/registrar", headers=gestor, json={"nome": "Agente Proj", "email": email, "senha": SENHA, "perfil": "ACS"}
    )
    assert r.status_code == 201
    return _login(client, email)


ATE = datetime.now(timezone.utc).replace(microsecond=0)
# casos por semana (da mais antiga à mais recente) em 4 semanas
CENARIO = {
    "Alfa": ([1, 2, 4, 6], -3.1000),  # cresce
    "Beta": ([1, 1, 1, 1], -3.1050),  # estável, ~0,55 km de Alfa
    "Gama": ([2, 2, 2, 2], -3.2000),  # estável, longe
    "Delta": ([0, 0, 0, 1], -3.3000),  # poucos casos
}


@pytest.fixture(scope="module")
def cenario(client, acs):
    n = 0
    for bairro, (serie, lat) in CENARIO.items():
        for i, qtd in enumerate(serie):
            semanas_atras = len(serie) - 1 - i  # 0 = semana mais recente
            for _ in range(qtd):
                n += 1
                quando = ATE - timedelta(days=7 * semanas_atras + 3, hours=n % 12)
                r = client.post(
                    "/atendimentos",
                    headers=acs,
                    json={
                        "paciente": {"nome": f"Paciente Proj {n}"},
                        "data_hora": quando.isoformat(),
                        "latitude": lat + 0.0000123,
                        "longitude": -60.0000456,
                        "municipio": MUN,
                        "bairro": bairro,
                    },
                )
                assert r.status_code == 201, r.text
    return n


def _projecao(client, cab, **params):
    base = {"semanas": 4, "horizonte": 2, "minimo_casos": 3, "raio_km": 3, "ate": ATE.isoformat()}
    base.update(params)
    r = client.get("/api/v1/mapa/projecao", headers=cab, params=base)
    assert r.status_code == 200, r.text
    return r.json()


def _meus(corpo):
    return {f["properties"]["localidade"].split(",")[0]: f for f in corpo["features"] if MUN in f["properties"]["localidade"]}


def test_endpoint_classifica_projeta_e_marca_a_vizinha(client, gestor, cenario):
    corpo = _projecao(client, gestor)
    f = _meus(corpo)
    alfa, beta, gama, delta = f["Alfa"]["properties"], f["Beta"]["properties"], f["Gama"]["properties"], f["Delta"]["properties"]

    assert alfa["serie_semanal"] == [1, 2, 4, 6] and alfa["total_periodo"] == 13
    assert alfa["tendencia"] == "CRESCENTE" and alfa["casos_previstos"] == [7.5, 9.2]
    assert beta["tendencia"] == "ESTAVEL" and beta["risco_de_expansao"] in ("MEDIO", "ALTO")
    assert beta["vizinhas_em_crescimento"][0]["localidade"].startswith("Alfa")
    assert gama["risco_de_expansao"] == "NENHUM"  # fora do raio de 3 km
    assert delta["tendencia"] == "DADOS_INSUFICIENTES" and delta["casos_previstos"] == []

    assert corpo["metodo"] == "regressao_linear_semanal" and "apoio à decisão" in corpo["aviso"]
    assert len(corpo["inicio_das_semanas"]) == 4 and len(corpo["inicio_das_semanas_projetadas"]) == 2


def test_raio_maior_alcanca_a_vizinha_distante(client, gestor, cenario):
    gama = _meus(_projecao(client, gestor, raio_km=15))["Gama"]["properties"]
    assert gama["risco_de_expansao"] != "NENHUM"


def test_coordenadas_arredondadas_e_sem_dados_de_paciente(client, gestor, cenario):
    corpo = _projecao(client, gestor)
    alfa = _meus(corpo)["Alfa"]
    lon, lat = alfa["geometry"]["coordinates"]
    assert (lon, lat) == (round(lon, 3), round(lat, 3))
    texto = str(alfa).lower()
    assert "paciente" not in texto and "atendimento_id" not in texto
    assert "lat" not in alfa["properties"] and "lon" not in alfa["properties"]


def test_atendimentos_depois_do_fim_da_janela_ficam_de_fora(client, gestor, cenario):
    antes = _meus(_projecao(client, gestor, ate=(ATE - timedelta(days=14)).isoformat()))
    assert antes["Alfa"]["properties"]["total_periodo"] == 3  # só as duas semanas mais antigas (2 + 1 casos)


def test_so_gestor_e_medico_enxergam(client, gestor, acs):
    assert client.get("/api/v1/mapa/projecao", headers=acs).status_code == 403
    assert client.get("/api/v1/mapa/projecao").status_code == 401
    assert client.get("/api/v1/mapa/projecao", headers=gestor).status_code == 200


@pytest.mark.parametrize(
    "params",
    [{"semanas": 2}, {"semanas": 27}, {"horizonte": 0}, {"horizonte": 5}, {"raio_km": 0}, {"minimo_casos": 0}],
)
def test_parametros_invalidos_retornam_422(client, gestor, params):
    assert client.get("/api/v1/mapa/projecao", headers=gestor, params=params).status_code == 422
