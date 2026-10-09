"""Testes do modelo de dengue (Python puro: não precisam de banco nem de rede)."""
import json
import math

import pytest

from app.services import ia_risco

MODELO = {
    "versao": "teste-1", "tipo": "regressao_logistica", "intercepto": -1.0, "limiar": 0.4, "idade_padrao_anos": 30.0,
    "features": [
        {"nome": "febre", "tipo": "binaria", "coef": 1.2, "rotulo": "Febre"},
        {"nome": "mialgia", "tipo": "binaria", "coef": 0.6, "rotulo": "Dor muscular"},
        {"nome": "dor_retro_orbital", "tipo": "binaria", "coef": 0.5, "rotulo": "Dor atrás dos olhos"},
        {"nome": "idade_anos", "tipo": "numerica", "coef": -0.3, "media": 30.0, "desvio": 15.0, "rotulo": "Idade"},
        {"nome": "sexo_f", "tipo": "binaria", "coef": 0.1, "rotulo": "Sexo feminino"},
    ],
}


def sig(z):
    return 1 / (1 + math.exp(-z))


def test_sem_sintomas_usa_so_o_intercepto_e_a_idade_media():
    assert ia_risco.pontuar(MODELO, [], 30, "M") == pytest.approx(sig(-1.0))


def test_soma_dos_coeficientes():
    p = ia_risco.pontuar(MODELO, ["Febre", "dor muscular"], 30, "M")
    assert p == pytest.approx(sig(-1.0 + 1.2 + 0.6))


def test_idade_e_sexo_entram_na_conta():
    p = ia_risco.pontuar(MODELO, ["febre"], 45, "F")  # idade 1 desvio acima da média
    assert p == pytest.approx(sig(-1.0 + 1.2 - 0.3 * 1 + 0.1))


def test_idade_ausente_ou_absurda_usa_a_idade_padrao():
    base = ia_risco.pontuar(MODELO, ["febre"], 30, "M")
    assert ia_risco.pontuar(MODELO, ["febre"], None, "M") == pytest.approx(base)
    assert ia_risco.pontuar(MODELO, ["febre"], 250, "M") == pytest.approx(base)


def test_sinonimos_e_acentos():
    a = ia_risco.pontuar(MODELO, ["Dor atrás dos olhos"], 30, "M")
    b = ia_risco.pontuar(MODELO, ["dor retro-orbital"], 30, "M")
    c = ia_risco.pontuar(MODELO, ["  DOR   ATRAS DOS OLHOS "], 30, "M")
    esperado = sig(-1.0 + 0.5)
    assert a == pytest.approx(esperado)
    assert b == pytest.approx(esperado)
    assert c == pytest.approx(esperado)


def test_sintoma_desconhecido_e_ignorado_mas_informado():
    marcadas, desconhecidos = ia_risco.sintomas_para_variaveis(MODELO, ["febre", "dor de dente"])
    assert marcadas == {"febre"} and desconhecidos == ["dor de dente"]


def test_extremos_nao_estouram():
    m = dict(MODELO, intercepto=-2000.0)
    assert 0.0 <= ia_risco.pontuar(m, [], 30, "M") < 1e-6
    m = dict(MODELO, intercepto=2000.0)
    assert ia_risco.pontuar(m, [], 30, "M") == pytest.approx(1.0)


def test_carregar_sem_arquivo_devolve_none(tmp_path):
    assert ia_risco.carregar(tmp_path / "nao_existe.json") is None


def test_carregar_arquivo_valido_e_invalido(tmp_path):
    ok = tmp_path / "m.json"
    ok.write_text(json.dumps(MODELO), encoding="utf-8")
    assert ia_risco.carregar(ok)["versao"] == "teste-1"
    ruim = tmp_path / "ruim.json"
    ruim.write_text(json.dumps(dict(MODELO, limiar=3)), encoding="utf-8")
    assert ia_risco.carregar(ruim) is None
    quebrado = tmp_path / "q.json"
    quebrado.write_text("{ isto não é json", encoding="utf-8")
    assert ia_risco.carregar(quebrado) is None


def test_carregar_rele_quando_o_arquivo_muda(tmp_path):
    f = tmp_path / "m.json"
    f.write_text(json.dumps(MODELO), encoding="utf-8")
    assert ia_risco.carregar(f)["versao"] == "teste-1"
    f.write_text(json.dumps(dict(MODELO, versao="teste-2")), encoding="utf-8")
    import os
    os.utime(f, (f.stat().st_atime + 5, f.stat().st_mtime + 5))
    assert ia_risco.carregar(f)["versao"] == "teste-2"


def test_rotulo_do_proprio_modelo_e_reconhecido():
    m = dict(MODELO, features=MODELO["features"] + [
        {"nome": "exantema", "tipo": "binaria", "coef": 0.7, "rotulo": "Manchas na pele (exantema)"}])
    marcadas, desconhecidos = ia_risco.sintomas_para_variaveis(m, ["Manchas na pele (exantema)"])
    assert marcadas == {"exantema"} and desconhecidos == []
