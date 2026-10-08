"""Testes da configuração segura (passo 13). Não precisam de banco.

Execute dentro do container:
    docker compose exec api pytest tests/test_config_segura.py -v
"""
import logging

import pytest

from app.core.config import Settings, exigir_config_segura

CHAVE_BOA = "a3f1c9d27b5e48f0916c2d7e8b4a5f10c3d9e6b7a8f2140596d3c7e1b8a4f2d6"


def _cfg(**mudancas) -> Settings:
    """Configuração válida; cada teste estraga só o que quer testar. _env_file=None ignora o .env real."""
    base = dict(
        AMBIENTE="producao",
        SECRET_KEY=CHAVE_BOA,
        DATABASE_URL="postgresql+psycopg2://medgraph:Uma-Senha-Forte-91@db:5432/medgraph",
        NEO4J_PASSWORD="Outra-Senha-Forte-77",
        ACCESS_TOKEN_EXPIRE_MINUTES=60,
        CORS_ORIGINS="https://medgraph-am.netlify.app",
    )
    base.update(mudancas)
    return Settings(_env_file=None, **base)


def test_configuracao_boa_nao_tem_problemas():
    assert _cfg().problemas_de_seguranca() == []
    assert exigir_config_segura(_cfg()) == []


@pytest.mark.parametrize(
    "chave",
    ["dev-only-change-me", "troque_por_uma_chave_longa_e_aleatoria", "changeme", "curta123"],
)
def test_chave_padrao_ou_curta_e_problema(chave):
    problemas = _cfg(SECRET_KEY=chave).problemas_de_seguranca()
    assert any("SECRET_KEY" in p for p in problemas)


def test_producao_recusa_subir_com_chave_padrao():
    with pytest.raises(RuntimeError) as erro:
        exigir_config_segura(_cfg(SECRET_KEY="dev-only-change-me"))
    assert "SECRET_KEY" in str(erro.value)


def test_desenvolvimento_so_avisa_e_nao_derruba(caplog):
    cfg = _cfg(AMBIENTE="desenvolvimento", SECRET_KEY="dev-only-change-me")
    with caplog.at_level(logging.WARNING, logger="medgraph.config"):
        problemas = exigir_config_segura(cfg)  # não pode levantar erro
    assert problemas
    assert "SECRET_KEY" in caplog.text


def test_senhas_padrao_do_banco_e_do_grafo_sao_problema():
    assert any("NEO4J" in p for p in _cfg(NEO4J_PASSWORD="neo4j").problemas_de_seguranca())
    assert any(
        "PostgreSQL" in p
        for p in _cfg(DATABASE_URL="postgresql+psycopg2://medgraph:medgraph@db:5432/medgraph").problemas_de_seguranca()
    )


def test_token_com_validade_longa_demais_e_problema():
    assert any("ACCESS_TOKEN" in p for p in _cfg(ACCESS_TOKEN_EXPIRE_MINUTES=10000).problemas_de_seguranca())


def test_cors_com_curinga_e_problema():
    assert any("curinga" in p for p in _cfg(CORS_ORIGINS="*").problemas_de_seguranca())


def test_producao_exige_https_nas_origens():
    assert any("HTTPS" in p for p in _cfg(CORS_ORIGINS="http://painel.exemplo.org").problemas_de_seguranca())
    # localhost é permitido mesmo sem HTTPS (demonstração local)
    assert _cfg(CORS_ORIGINS="http://localhost:5500").problemas_de_seguranca() == []


@pytest.mark.parametrize("valor", ["producao", "Produção", "PRODUCTION", "prod"])
def test_nomes_de_producao_sao_reconhecidos(valor):
    assert _cfg(AMBIENTE=valor).em_producao is True


def test_padrao_e_desenvolvimento():
    assert Settings(_env_file=None).em_producao is False
