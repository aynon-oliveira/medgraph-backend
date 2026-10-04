import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal, engine, Base

@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c

@pytest.fixture
def token_headers(client):
    # Regista ou autentica o utilizador de teste para gerar o token JWT
    login_response = client.post(
        "/auth/login",
        data={"username": "gestor@medgraph.com", "password": "password123"}
    )
    if login_response.status_code != 200:
        login_response = client.post(
            "/api/v1/auth/login",
            data={"username": "gestor@medgraph.com", "password": "password123"}
        )
    
    if login_response.status_code == 200:
        token = login_response.json().get("access_token")
        return {"Authorization": f"Bearer {token}"}
    
    return {}
