import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.deps import get_current_user

client = TestClient(app)

def test_obter_mapa_focos_geojson_requer_autenticacao():
    # Garante que as sobreposições de dependência não estão ativas neste teste
    app.dependency_overrides.clear()
    response = client.get("/api/v1/mapa/focos")
    assert response.status_code == 401

def test_obter_mapa_focos_geojson_sucesso():
    # 1. Cria um utilizador simulado para contornar a autenticação no teste
    class UsuarioMock:
        id = "123"
        email = "gestor@medgraph.com"
        perfil = "GESTOR"

    # 2. Injeta o utilizador diretamente na dependência get_current_user do FastAPI
    app.dependency_overrides[get_current_user] = lambda: UsuarioMock()

    try:
        response = client.get("/api/v1/mapa/focos")
        assert response.status_code == 200
        dados = response.json()
        assert dados["type"] == "FeatureCollection"
        assert "features" in dados
    finally:
        # Limpa o override após o teste para não afetar outros testes
        app.dependency_overrides.clear()
