"""Testes unitários da segurança (não precisam de banco)."""
import uuid

import pytest
from jose import JWTError

from app.core.security import criar_token, decodificar_token, hash_senha, verificar_senha
from app.models.enums import Perfil


def test_hash_nao_guarda_senha_em_texto_puro():
    h = hash_senha("minhasenha123")
    assert h != "minhasenha123"
    assert verificar_senha("minhasenha123", h)


def test_senha_errada_e_rejeitada():
    h = hash_senha("minhasenha123")
    assert not verificar_senha("outrasenha", h)


def test_token_guarda_id_e_perfil():
    uid = uuid.uuid4()
    token = criar_token(uid, Perfil.MEDICO)
    dados = decodificar_token(token)
    assert dados["sub"] == str(uid)
    assert dados["perfil"] == "MEDICO"


def test_token_adulterado_e_rejeitado():
    token = criar_token(uuid.uuid4(), Perfil.ACS)
    with pytest.raises(JWTError):
        decodificar_token(token + "x")
