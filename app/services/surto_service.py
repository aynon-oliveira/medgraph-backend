"""Projeção do avanço de surtos (RF10) — linha de base estatística, transparente e sem banco.

O que faz, em linguagem simples:
  1. Para cada localidade, olha quantos atendimentos houve em cada semana (janelas de 7 dias).
  2. Ajusta uma reta (mínimos quadrados) a essa série e a estende para as próximas semanas.
  3. Classifica a tendência (CRESCENTE, ESTAVEL, DECRESCENTE) pela inclinação relativa da reta.
  4. Aponta localidades próximas (raio em km) de outras em crescimento: são as candidatas à expansão do surto.

O que NÃO é: não é a rede GCN/GAT do trabalho futuro e não é um modelo epidemiológico validado.
É uma estimativa de apoio à decisão (RN01): quem decide é o gestor/médico.
Sem dependências externas e sem acesso a banco: as funções são puras e testáveis.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

# Inclinação relativa mínima (casos/semana ÷ média semanal) para dizer que há crescimento ou queda.
LIMIAR_TENDENCIA = 0.10
# A projeção não pode passar de TETO_PROJECAO × o maior valor já visto na série (evita extrapolação absurda).
TETO_PROJECAO = 3.0
# Pressão de vizinhança (soma de casos previstos nas vizinhas em crescimento, ponderada pela distância).
PRESSAO_ALTA = 3.0
PRESSAO_MEDIA = 1.0

TENDENCIAS = ("CRESCENTE", "ESTAVEL", "DECRESCENTE", "DADOS_INSUFICIENTES")

AVISO = (
    "Estimativa estatística de apoio à decisão (reta ajustada às contagens semanais). "
    "Não substitui a avaliação do gestor e dos profissionais de saúde, e não é o modelo de grafos GCN/GAT."
)


def regressao_linear(serie: list[float]) -> tuple[float, float]:
    """Reta y = a + b·x pelos mínimos quadrados, com x = 0, 1, 2... Retorna (a, b)."""
    n = len(serie)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        return float(serie[0]), 0.0
    media_x = (n - 1) / 2
    media_y = sum(serie) / n
    sxx = sum((x - media_x) ** 2 for x in range(n))
    sxy = sum((x - media_x) * (y - media_y) for x, y in enumerate(serie))
    b = sxy / sxx
    return media_y - b * media_x, b


def classificar_tendencia(serie: list[float], inclinacao: float, total: int, minimo_casos: int) -> str:
    if total < minimo_casos:
        return "DADOS_INSUFICIENTES"
    media = sum(serie) / len(serie) if serie else 0.0
    relativa = inclinacao / max(media, 1.0)
    if relativa >= LIMIAR_TENDENCIA:
        return "CRESCENTE"
    if relativa <= -LIMIAR_TENDENCIA:
        return "DECRESCENTE"
    return "ESTAVEL"


def projetar_serie(serie: list[float], horizonte: int) -> list[float]:
    """Casos previstos para as próximas `horizonte` semanas (nunca negativos, com teto)."""
    a, b = regressao_linear(serie)
    n = len(serie)
    teto = TETO_PROJECAO * max(max(serie, default=0), 1)
    return [round(min(max(a + b * (n - 1 + k), 0.0), teto), 1) for k in range(1, horizonte + 1)]


def distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância em linha reta entre dois pontos (fórmula de haversine)."""
    r = 6371.0088
    f1, f2 = math.radians(lat1), math.radians(lat2)
    df, dl = f2 - f1, math.radians(lon2 - lon1)
    h = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def nivel_de_pressao(pressao: float) -> str:
    if pressao >= PRESSAO_ALTA:
        return "ALTO"
    if pressao >= PRESSAO_MEDIA:
        return "MEDIO"
    return "BAIXO"


def inicio_das_semanas(ate: datetime, semanas: int) -> list[datetime]:
    """Início de cada janela, da mais antiga para a mais recente (a última termina em `ate`)."""
    return [ate - timedelta(days=7 * (semanas - i)) for i in range(semanas)]


def prever_avanco(
    localidades: dict[str, dict[str, Any]],
    *,
    semanas: int,
    horizonte: int,
    minimo_casos: int,
    raio_km: float,
) -> list[dict[str, Any]]:
    """Calcula tendência, projeção e risco de expansão por localidade.

    `localidades`: chave -> {"nome", "serie" (contagens semanais, da mais antiga à mais recente, tamanho `semanas`),
    "alto" (casos de alto risco no período), "lat", "lon", "focos" (opcional)}.
    """
    resultado: dict[str, dict[str, Any]] = {}
    for chave, loc in localidades.items():
        serie = [float(x) for x in loc["serie"]]
        total = int(sum(serie))
        _, b = regressao_linear(serie)
        tendencia = classificar_tendencia(serie, b, total, minimo_casos)
        resultado[chave] = {
            "chave": chave,
            "localidade": loc["nome"],
            "lat": loc["lat"],
            "lon": loc["lon"],
            "focos": loc.get("focos"),
            "serie_semanal": [int(x) for x in serie],
            "total_periodo": total,
            "alto_risco_periodo": int(loc.get("alto", 0)),
            "inclinacao_casos_por_semana": round(b, 2),
            "tendencia": tendencia,
            "casos_previstos": projetar_serie(serie, horizonte) if tendencia != "DADOS_INSUFICIENTES" else [],
        }

    # Expansão: quem não está em crescimento, mas tem vizinha (até raio_km) que está.
    emissoras = [r for r in resultado.values() if r["tendencia"] == "CRESCENTE"]
    for r in resultado.values():
        vizinhas, pressao = [], 0.0
        if r["tendencia"] != "CRESCENTE":
            for e in emissoras:
                d = distancia_km(r["lat"], r["lon"], e["lat"], e["lon"])
                if d <= raio_km:
                    vizinhas.append({"localidade": e["localidade"], "distancia_km": round(d, 2)})
                    pressao += (e["casos_previstos"][0] if e["casos_previstos"] else 0.0) / (1.0 + d)
        vizinhas.sort(key=lambda v: v["distancia_km"])
        r["vizinhas_em_crescimento"] = vizinhas
        r["pressao_de_vizinhanca"] = round(pressao, 2)
        r["risco_de_expansao"] = nivel_de_pressao(pressao) if vizinhas else "NENHUM"

    return sorted(
        resultado.values(),
        key=lambda r: (-(r["casos_previstos"][-1] if r["casos_previstos"] else 0), -r["total_periodo"], r["chave"]),
    )
