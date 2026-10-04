# app/routers/mapa.py
from typing import Dict, Any
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.usuario import Usuario

router = APIRouter(prefix="/api/v1/mapa", tags=["Mapa de Risco"])

@router.get("/focos", response_model=Dict[str, Any])
def obter_focos_dengue_geojson(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Retorna os pontos de atendimentos e densidade de casos de Dengue no formato GeoJSON (RF10).
    Acessível para visualização nos painéis de vigilância.
    """
    # Consulta SQL com PostGIS extraindo latitude/longitude no formato GeoJSON
    query = text("""
        SELECT 
            id,
            paciente_id,
            nivel_risco,
            status_sincronizacao,
            data_hora,
            ST_X(localizacao::geometry) as longitude,
            ST_Y(localizacao::geometry) as latitude
        FROM atendimentos
        WHERE localizacao IS NOT NULL
    """)
    
    resultados = db.execute(query).fetchall()
    
    features = []
    for row in resultados:
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [row.longitude, row.latitude]
            },
            "properties": {
                "atendimento_id": str(row.id),
                "paciente_id": str(row.paciente_id),
                "nivel_risco": row.nivel_risco,
                "data_hora": row.data_hora.isoformat() if row.data_hora else None
            }
        })
        
    return {
        "type": "FeatureCollection",
        "features": features
    }