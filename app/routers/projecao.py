from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_perfil
from app.models import Usuario
from app.routers.mapa import CASAS_DECIMAIS, LEITORES, _focos_por_localidade
from app.services import neo4j_service, surto_service

router = APIRouter(prefix="/api/v1/mapa", tags=["Mapa de Risco"])


@router.get("/projecao", response_model=dict[str, Any])
def projecao_do_avanco(
    semanas: int = Query(8, ge=3, le=26, description="Quantas semanas do passado entram no cálculo"),
    horizonte: int = Query(2, ge=1, le=4, description="Quantas semanas à frente projetar"),
    minimo_casos: int = Query(5, ge=1, le=200, description="Abaixo disso a localidade fica 'sem dados suficientes'"),
    raio_km: float = Query(3.0, gt=0, le=30, description="Distância para considerar localidades vizinhas"),
    ate: datetime | None = Query(None, description="Fim da janela (padrão: agora). ISO com fuso"),
    db: Session = Depends(get_db),
    _: Usuario = Depends(require_perfil(*LEITORES)),
) -> dict[str, Any]:
    """Projeção do avanço de surtos por localidade (RF10), em GeoJSON.

    Conta atendimentos por semana (janelas de 7 dias terminando em `ate`), ajusta uma reta, projeta as próximas
    semanas e marca as localidades vizinhas de áreas em crescimento. É apoio à decisão (RN01), não diagnóstico.
    Coordenadas arredondadas (LGPD); nenhum dado de paciente é devolvido.
    """
    fim = ate or datetime.now(timezone.utc)
    inicios = surto_service.inicio_das_semanas(fim, semanas)

    consulta = text(
        """
        SELECT municipio,
               bairro,
               CAST(FLOOR(EXTRACT(EPOCH FROM (CAST(:fim AS timestamptz) - data_hora)) / 604800) AS integer) AS janela,
               count(*) AS total,
               count(*) FILTER (WHERE nivel_risco::text = 'ALTO') AS alto_risco,
               ST_X(ST_Centroid(ST_Collect(localizacao::geometry))) AS longitude,
               ST_Y(ST_Centroid(ST_Collect(localizacao::geometry))) AS latitude
        FROM atendimentos
        WHERE municipio IS NOT NULL
          AND data_hora <= CAST(:fim AS timestamptz)
          AND data_hora > CAST(:fim AS timestamptz) - (:dias * INTERVAL '1 day')
        GROUP BY municipio, bairro, janela
        """
    )
    linhas = db.execute(consulta, {"fim": fim, "dias": 7 * semanas}).all()

    # Junta variações de escrita da mesma localidade (mesma chave do grafo). janela 0 = semana mais recente.
    grupos: dict[str, dict[str, Any]] = {}
    for l in linhas:
        loc = neo4j_service.montar_localidade(l.municipio, l.bairro)
        if loc is None or not 0 <= l.janela < semanas:
            continue
        g = grupos.setdefault(
            loc["chave"],
            {"nome": loc["nome"], "serie": [0] * semanas, "alto": 0, "soma_lon": 0.0, "soma_lat": 0.0, "peso": 0},
        )
        g["serie"][semanas - 1 - l.janela] += l.total  # da mais antiga (0) à mais recente (semanas-1)
        g["alto"] += l.alto_risco
        g["soma_lon"] += l.longitude * l.total
        g["soma_lat"] += l.latitude * l.total
        g["peso"] += l.total

    focos = _focos_por_localidade()
    for chave, g in grupos.items():
        g["lon"] = g["soma_lon"] / g["peso"]
        g["lat"] = g["soma_lat"] / g["peso"]
        g["focos"] = focos.get(chave, 0) if focos is not None else None

    previsoes = surto_service.prever_avanco(
        grupos, semanas=semanas, horizonte=horizonte, minimo_casos=minimo_casos, raio_km=raio_km
    )

    features = []
    for p in previsoes:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(p["lon"], CASAS_DECIMAIS), round(p["lat"], CASAS_DECIMAIS)],
                },
                "properties": {k: v for k, v in p.items() if k not in ("lat", "lon")},
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
        "grafo_disponivel": focos is not None,
        "metodo": "regressao_linear_semanal",
        "aviso": surto_service.AVISO,
        "parametros": {
            "semanas": semanas,
            "horizonte_semanas": horizonte,
            "minimo_casos": minimo_casos,
            "raio_km": raio_km,
            "fim_da_janela": fim.isoformat(),
        },
        "inicio_das_semanas": [i.isoformat() for i in inicios],
        "inicio_das_semanas_projetadas": [
            (fim + timedelta(days=7 * k)).isoformat() for k in range(0, horizonte)
        ],
    }
