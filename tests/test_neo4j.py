# tests/test_neo4j.py
import pytest
from app.services.neo4j_service import neo4j_service

def test_sincronizacao_no_neo4j():
    """
    Valida a gravação sem erros no Neo4j.
    """
    try:
        neo4j_service.sincronizar_atendimento_grafo(
            paciente_id="paciente-teste-123",
            sintomas=["febre", "cefaleia", "dor_retro_orbitaria"],
            codigo_ibge="1302603",  # Código IBGE de Manaus
            localidade_nome="Manaus - Zona Sul"
        )
        assert True
    except Exception as e:
        pytest.fail(f"Erro ao conectar ou gravar no Neo4j: {e}")