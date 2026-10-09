"""Pontuação de dengue por regressão logística (apoio à triagem, RN01).

O modelo é treinado FORA da API (ml/treinar_risco.py) com dados reais do SINAN e exportado em
`app/ia/modelo_risco.json`. Aqui só se lê esse arquivo e se faz a conta, em Python puro:

    z = intercepto + soma(coef * variavel)      p = 1 / (1 + e^-z)

Por ser só uma conta, o MESMO modelo roda no site do ACS, sem internet (app.js repete esta função).
Se o arquivo não existir (modelo ainda não treinado), `carregar()` devolve None e a API segue sem ele.
"""
from __future__ import annotations

import json
import math
import os
import unicodedata
from pathlib import Path

CAMINHO_PADRAO = Path(__file__).resolve().parent.parent / "ia" / "modelo_risco.json"

# Textos que o agente pode digitar/escolher -> nome da variável no modelo.
SINONIMOS = {
    "febre": "febre",
    "dor muscular": "mialgia", "mialgia": "mialgia", "dor no corpo": "mialgia",
    "dor de cabeca": "cefaleia", "cefaleia": "cefaleia",
    "manchas na pele": "exantema", "exantema": "exantema", "manchas vermelhas": "exantema", "erupcao": "exantema",
    "vomito": "vomito", "vomitos": "vomito",
    "nausea": "nausea", "enjoo": "nausea",
    "dor nas costas": "dor_costas",
    "conjuntivite": "conjuntivite", "olhos vermelhos": "conjuntivite",
    "artrite": "artrite",
    "dor nas articulacoes": "artralgia", "dor intensa nas articulacoes": "artralgia", "artralgia": "artralgia",
    "petequias": "petequias",
    "leucopenia": "leucopenia",
    "prova do laco positiva": "prova_laco", "prova do laco": "prova_laco",
    "dor atras dos olhos": "dor_retro_orbital", "dor retro-orbital": "dor_retro_orbital",
    "dor retroorbital": "dor_retro_orbital", "dor retro orbital": "dor_retro_orbital",
}

_cache: dict = {"caminho": None, "mtime": None, "modelo": None}


def _normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.lower().strip().split())


def caminho_do_modelo() -> Path:
    return Path(os.environ.get("MEDGRAPH_MODELO_RISCO") or CAMINHO_PADRAO)


def validar(modelo: dict) -> None:
    """Falha com ValueError se o arquivo não tiver a forma esperada (evita servir um modelo quebrado)."""
    if modelo.get("tipo") != "regressao_logistica":
        raise ValueError("tipo de modelo não suportado")
    if not isinstance(modelo.get("intercepto"), (int, float)):
        raise ValueError("intercepto ausente")
    feats = modelo.get("features")
    if not isinstance(feats, list) or not feats:
        raise ValueError("features ausentes")
    for f in feats:
        if not isinstance(f.get("nome"), str) or not isinstance(f.get("coef"), (int, float)):
            raise ValueError("feature inválida")
        if f.get("tipo") == "numerica" and not (f.get("desvio") and isinstance(f.get("media"), (int, float))):
            raise ValueError("feature numérica sem média/desvio")
    if not isinstance(modelo.get("limiar"), (int, float)) or not 0 < modelo["limiar"] < 1:
        raise ValueError("limiar inválido")


def carregar(caminho: Path | None = None) -> dict | None:
    """Lê o modelo (com cache; relê se o arquivo mudar). Devolve None se não existir ou for inválido."""
    alvo = Path(caminho) if caminho else caminho_do_modelo()
    try:
        mtime = alvo.stat().st_mtime
    except OSError:
        _cache.update(caminho=None, mtime=None, modelo=None)
        return None
    if _cache["caminho"] == str(alvo) and _cache["mtime"] == mtime:
        return _cache["modelo"]
    try:
        modelo = json.loads(alvo.read_text(encoding="utf-8"))
        validar(modelo)
    except (OSError, ValueError):
        modelo = None
    _cache.update(caminho=str(alvo), mtime=mtime, modelo=modelo)
    return modelo


def sintomas_para_variaveis(modelo: dict, sintomas: list[str]) -> tuple[set[str], list[str]]:
    """Separa os sintomas em (variáveis do modelo marcadas, textos que o modelo não conhece)."""
    conhecidas = {f["nome"] for f in modelo["features"] if f.get("tipo") == "binaria"}
    por_rotulo = {_normalizar(f["rotulo"]): f["nome"] for f in modelo["features"] if f.get("rotulo")}
    marcadas: set[str] = set()
    desconhecidos: list[str] = []
    for s in sintomas or []:
        chave = _normalizar(s)
        nome = chave if chave in conhecidas else (por_rotulo.get(chave) or SINONIMOS.get(chave))
        if nome in conhecidas:
            marcadas.add(nome)
        elif chave:
            desconhecidos.append(s)
    return marcadas, desconhecidos


def pontuar(modelo: dict, sintomas: list[str], idade_anos: float | None = None, sexo: str | None = None,
            dias_sintomas: float | None = None) -> float:
    """Probabilidade estimada (0 a 1). Sintoma não informado conta como ausente; idade e dias ausentes usam o valor típico."""
    marcadas, _ = sintomas_para_variaveis(modelo, sintomas)
    z = float(modelo["intercepto"])
    for f in modelo["features"]:
        nome = f["nome"]
        if f.get("tipo") == "numerica" and nome == "dias_sintomas":
            padrao = modelo.get("dias_padrao", f["media"])
            dias = min(dias_sintomas, 14) if dias_sintomas is not None and 0 <= dias_sintomas <= 30 else padrao   # o treino limita a 14
            z += f["coef"] * ((dias - f["media"]) / f["desvio"])
        elif f.get("tipo") == "numerica":  # idade
            idade = idade_anos if idade_anos is not None and 0 <= idade_anos <= 110 else modelo.get("idade_padrao_anos", f["media"])
            z += f["coef"] * ((idade - f["media"]) / f["desvio"])
        elif nome == "sexo_f":
            z += f["coef"] * (1.0 if (sexo or "").upper() == "F" else 0.0)
        else:
            z += f["coef"] * (1.0 if nome in marcadas else 0.0)
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def informacao_publica(modelo: dict) -> dict:
    """O que o site precisa para calcular offline (o arquivo inteiro, sem nada sensível)."""
    return modelo
