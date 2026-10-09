"""Emergencia: define uma NOVA senha para um usuario (normalmente o GESTOR que esqueceu a sua).

Roda DENTRO do container da API, no seu computador (quem tem acesso ao servidor tem acesso ao banco):

    docker compose exec api python -m scripts.redefinir_gestor gestor@exemplo.com

Mostra uma senha temporaria (so desta vez), derruba as sessoes abertas desse usuario e reativa a conta se
estiver desativada. Depois entre no site e troque a senha em "Minha senha".
"""
import secrets
import string
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_senha
from app.models import Usuario


def gerar_senha() -> str:
    alfabeto = string.ascii_letters + string.digits
    while True:
        s = "".join(secrets.choice(alfabeto) for _ in range(12))
        if any(c.isalpha() for c in s) and any(c.isdigit() for c in s):
            return s


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Uso: python -m scripts.redefinir_gestor EMAIL")
        return 2
    email = argv[1].strip().lower()
    with SessionLocal() as db:
        usuario = db.scalar(select(Usuario).where(Usuario.email == email))
        if usuario is None:
            print(f"Nao existe usuario com o e-mail {email}.")
            return 1
        senha = gerar_senha()
        usuario.senha_hash = hash_senha(senha)
        usuario.senha_alterada_em = datetime.now(timezone.utc)
        usuario.ativo = True
        db.commit()
        print(f"Usuario: {usuario.nome} ({usuario.perfil.value if hasattr(usuario.perfil, 'value') else usuario.perfil})")
        print(f"Senha temporaria: {senha}")
        print("Anote agora (ela nao sera mostrada de novo) e troque em 'Minha senha' depois de entrar.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
