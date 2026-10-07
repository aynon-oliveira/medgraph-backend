import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_perfil
from app.models import NivelRisco, Perfil, Usuario
from app.services import neo4j_service

logger = logging.getLogger("medgraph.mapa")

router = APIRouter(prefix="/api/v1/mapa", tags=["Mapa de Risco"])

# Quem pode ver o mapa: dados de saúde e localização não são para todos os perfis.
LEITORES = (Perfil.GESTOR, Perfil.MEDICO)

# Privacidade (LGPD): as coordenadas saem arredondadas (3 casas decimais = cerca de 110 m),
# para não apontar a casa exata do paciente. O paciente_id nunca é devolvido.
CASAS_DECIMAIS = 3


@router.get("/atendimentos", response_model=dict[str, Any])
@router.get("/focos", response_model=dict[str, Any], deprecated=True)  # nome antigo, mantido por compatibilidade
def mapa_de_atendimentos(
    nivel_risco: NivelRisco | None = None,
    desde: datetime | None = None,
    ate: datetime | None = None,
    limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
    _: Usuario = Depends(require_perfil(*LEITORES)),
) -> dict[str, Any]:
    """Pontos dos atendimentos em GeoJSON (RF10), do mais recente para o mais antigo.

    Filtros: nivel_risco, desde, ate (datas ISO com fuso, ex.: 2026-10-01T00:00:00-04:00) e limit.
    """
    consulta = text(
        """
        SELECT id,
               nivel_risco::text AS nivel_risco,
               status_validacao::text AS status_validacao,
               data_hora,
               municipio,
               bairro,
               ST_X(localizacao::geometry) AS longitude,
               ST_Y(localizacao::geometry) AS latitude
        FROM atendimentos
        WHERE localizacao IS NOT NULL
          AND (CAST(:nivel AS text) IS NULL OR nivel_risco::text = CAST(:nivel AS text))
          AND (CAST(:desde AS timestamptz) IS NULL OR data_hora >= CAST(:desde AS timestamptz))
          AND (CAST(:ate AS timestamptz) IS NULL OR data_hora <= CAST(:ate AS timestamptz))
        ORDER BY data_hora DESC
        LIMIT :limite
        """
    )
    linhas = db.execute(
        consulta,
        {
            "nivel": nivel_risco.value if nivel_risco else None,
            "desde": desde,
            "ate": ate,
            "limite": limit,
        },
    ).all()

    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [round(l.longitude, CASAS_DECIMAIS), round(l.latitude, CASAS_DECIMAIS)],
            },
            "properties": {
                "atendimento_id": str(l.id),
                "nivel_risco": l.nivel_risco,
                "status_validacao": l.status_validacao,
                "data_hora": l.data_hora.isoformat() if l.data_hora else None,
                "municipio": l.municipio,
                "bairro": l.bairro,
            },
        }
        for l in linhas
    ]
    return {"type": "FeatureCollection", "features": features}


def _focos_por_localidade() -> dict[str, int] | None:
    """Número de focos do vetor por localidade, vindo do grafo. None se o Neo4j não responder."""
    try:
        return {l["chave"]: l["focos"] for l in neo4j_service.localidades(1000)}
    except Exception:  # noqa: BLE001
        logger.warning("Grafo indisponível; o mapa de densidade segue sem os focos.", exc_info=True)
        return None


@router.get("/densidade", response_model=dict[str, Any])
def densidade_por_localidade(
    desde: datetime | None = None,
    ate: datetime | None = None,
    db: Session = Depends(get_db),
    _: Usuario = Depends(require_perfil(*LEITORES)),
) -> dict[str, Any]:
    """Casos por localidade (município e bairro) em GeoJSON, com os focos do vetor do grafo.

    Cada ponto fica no centro dos atendimentos da localidade. Use desde/ate para comparar períodos.
    """
    consulta = text(
        """
        SELECT municipio,
               bairro,
               count(*) AS total,
               count(*) FILTER (WHERE nivel_risco::text = 'ALTO') AS alto_risco,
               ST_X(ST_Centroid(ST_Collect(localizacao::geometry))) AS longitude,
               ST_Y(ST_Centroid(ST_Collect(localizacao::geometry))) AS latitude
        FROM atendimentos
        WHERE municipio IS NOT NULL
          AND (CAST(:desde AS timestamptz) IS NULL OR data_hora >= CAST(:desde AS timestamptz))
          AND (CAST(:ate AS timestamptz) IS NULL OR data_hora <= CAST(:ate AS timestamptz))
        GROUP BY municipio, bairro
        """
    )
    linhas = db.execute(consulta, {"desde": desde, "ate": ate}).all()

    # Junta variações de escrita da mesma localidade usando a mesma chave do grafo
    grupos: dict[str, dict[str, Any]] = {}
    for l in linhas:
        localidade = neo4j_service.montar_localidade(l.municipio, l.bairro)
        if localidade is None:
            continue
        g = grupos.setdefault(
            localidade["chave"],
            {"localidade": localidade, "total": 0, "alto": 0, "soma_lon": 0.0, "soma_lat": 0.0},
        )
        g["total"] += l.total
        g["alto"] += l.alto_risco
        g["soma_lon"] += l.longitude * l.total
        g["soma_lat"] += l.latitude * l.total

    focos = _focos_por_localidade()

    features = []
    for chave, g in sorted(grupos.items(), key=lambda item: item[1]["total"], reverse=True):
        total = g["total"]
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        round(g["soma_lon"] / total, CASAS_DECIMAIS),
                        round(g["soma_lat"] / total, CASAS_DECIMAIS),
                    ],
                },
                "properties": {
                    "localidade": g["localidade"]["nome"],
                    "chave": chave,
                    "total_atendimentos": total,
                    "alto_risco": g["alto"],
                    "proporcao_alto_risco": round(g["alto"] / total, 3),
                    "focos": (focos.get(chave, 0) if focos is not None else None),
                },
            }
        )

    return {"type": "FeatureCollection", "features": features, "grafo_disponivel": focos is not None}
