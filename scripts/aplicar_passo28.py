"""Passo 28 - aplica as mudancas de seguranca nos arquivos existentes (com copia de seguranca *.bak-passo28).

Uso, na pasta medgraph-backend (depois de copiar os arquivos novos do passo):
    python scripts/aplicar_passo28.py

Pode rodar de novo sem problema: o que ja foi aplicado e pulado. Se algum trecho nao for encontrado
(arquivo diferente do esperado), o script PARA antes de gravar qualquer coisa nesse arquivo e avisa.
"""
import pathlib
import shutil
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
avisos = []


def ler(caminho):
    bruto = caminho.read_bytes().decode("utf-8")
    crlf = "\r\n" in bruto
    return bruto.replace("\r\n", "\n"), crlf


def gravar(caminho, texto, crlf):
    shutil.copy2(caminho, caminho.with_name(caminho.name + ".bak-passo28"))
    caminho.write_bytes((texto.replace("\n", "\r\n") if crlf else texto).encode("utf-8"))


def aplicar(rel, trocas, ja_aplicado):
    """trocas: lista de (antigo, novo). ja_aplicado: texto que so existe depois de aplicar."""
    caminho = RAIZ / rel
    if not caminho.exists():
        sys.exit(f"ERRO: nao achei {rel}. Rode este script dentro da pasta medgraph-backend.")
    texto, crlf = ler(caminho)
    if ja_aplicado in texto:
        print(f"  {rel}: ja estava aplicado, pulei.")
        return
    for antigo, novo in trocas:
        n = texto.count(antigo)
        if n != 1:
            sys.exit(f"ERRO em {rel}: esperava 1 ocorrencia do trecho abaixo e achei {n}. Nada foi gravado neste arquivo.\n---\n{antigo}\n---\n"
                     "Me mande uma foto desta mensagem.")
        texto = texto.replace(antigo, novo)
    gravar(caminho, texto, crlf)
    print(f"  {rel}: aplicado.")


print("Passo 28 - aplicando...")

# 1) Bancos so acessiveis neste computador (nao pela rede local)
aplicar("docker-compose.yml", [
    ('"5432:5432"', '"127.0.0.1:5432:5432"'),
    ('"7474:7474"', '"127.0.0.1:7474:7474"'),
    ('"7687:7687"', '"127.0.0.1:7687:7687"'),
], '"127.0.0.1:5432:5432"')

# 2) Limite de tentativas de login
aplicar("app/routers/auth.py", [
    ("from app.core.deps import get_current_user, get_current_user_optional\n",
     "from app.core.deps import get_current_user, get_current_user_optional\nfrom app.core.limitador import limitador_login\n"),
    ('''    usuario = db.scalar(select(Usuario).where(Usuario.email == form.username.strip().lower()))
    if (
        usuario is None
        or not usuario.ativo
        or not verificar_senha(form.password, usuario.senha_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha incorretos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Token(''',
     '''    chave = limitador_login.chave(form.username)
    espera = limitador_login.segundos_bloqueado(chave)
    if espera > 0:
        minutos = max(1, (espera + 59) // 60)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Muitas tentativas de entrada. Tente de novo em {minutos} minuto(s).",
            headers={"Retry-After": str(espera)},
        )
    usuario = db.scalar(select(Usuario).where(Usuario.email == form.username.strip().lower()))
    if (
        usuario is None
        or not usuario.ativo
        or not verificar_senha(form.password, usuario.senha_hash)
    ):
        limitador_login.registrar_falha(chave)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha incorretos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    limitador_login.registrar_sucesso(chave)
    return Token('''),
], "limitador_login.registrar_sucesso")

# 3) Foto sem GPS/EXIF no disco e antes de ir ao Gemini
aplicar("app/services/midia_service.py", [
    ("from app.core.config import settings\n", "from app.core.config import settings\nfrom app.services.imagem_limpa import remover_metadados\n"),
    ('''    except Exception:
        destino.unlink(missing_ok=True)  # não deixa arquivo pela metade
        raise
    return destino_rel
''', '''    except Exception:
        destino.unlink(missing_ok=True)  # não deixa arquivo pela metade
        raise
    if categoria == "foto":
        limpar_metadados_do_arquivo(destino)  # passo 28: tira GPS, modelo do celular e data da foto
    return destino_rel
'''),
    ("def salvar(categoria: str, atendimento_id: uuid.UUID, arquivo: UploadFile) -> str:",
     '''def limpar_metadados_do_arquivo(destino: Path) -> None:
    """Regrava a foto sem EXIF/XMP/comentários (os pixels não mudam). Se algo falhar, o upload segue."""
    try:
        original = destino.read_bytes()
        limpo = remover_metadados(original)
        if limpo != original:
            destino.write_bytes(limpo)
    except OSError:
        pass


def salvar(categoria: str, atendimento_id: uuid.UUID, arquivo: UploadFile) -> str:'''),
], "limpar_metadados_do_arquivo(destino)")

aplicar("app/routers/assistente.py", [
    ("from app.services import auditoria_service, gemini_service, midia_service\n",
     "from app.services import auditoria_service, gemini_service, midia_service\nfrom app.services.imagem_limpa import remover_metadados\n"),
    ("    imagem = caminho.read_bytes()\n", "    imagem = remover_metadados(caminho.read_bytes())  # passo 28: fotos antigas também saem sem GPS\n"),
], "remover_metadados(caminho.read_bytes())")

# 4) /health sem mensagens internas de erro
aplicar("app/routers/health.py", [
    ("from fastapi import APIRouter, Depends\n", "import logging\n\nfrom fastapi import APIRouter, Depends\n"),
    ('router = APIRouter(tags=["Saúde da API"])\n', 'router = APIRouter(tags=["Saúde da API"])\nlogger = logging.getLogger("medgraph.health")\n'),
    ('        estado["postgres_detalhe"] = str(exc)[:120]\n', '        logger.warning("Falha no PostgreSQL: %s", str(exc)[:200])\n'),
    ('        estado["neo4j_detalhe"] = str(exc)[:120]\n', '        logger.warning("Falha no Neo4j: %s", str(exc)[:200])\n'),
], 'logger.warning("Falha no Neo4j')

# 5) Testes antigos: zerar o limite de login antes de cada teste
caminho = RAIZ / "tests" / "conftest.py"
texto, crlf = ler(caminho)
if "_zerar_limitador_de_login" in texto:
    print("  tests/conftest.py: ja estava aplicado, pulei.")
else:
    texto = texto.rstrip("\n") + '''


@pytest.fixture(autouse=True)
def _zerar_limitador_de_login():
    """Passo 28: o limite de tentativas de login não pode vazar de um teste para outro."""
    from app.core.limitador import limitador_login

    limitador_login.zerar()
    yield
'''
    gravar(caminho, texto, crlf)
    print("  tests/conftest.py: aplicado.")

# 6) Aviso no ligar.ps1 se ainda estiver em modo desenvolvimento
aplicar("ligar.ps1", [
    ("Dizer '     API, PostgreSQL, PostGIS e Neo4j: ok.' 'Green'\n",
     "Dizer '     API, PostgreSQL, PostGIS e Neo4j: ok.' 'Green'\n"
     "$arqEnv = Join-Path $PSScriptRoot '.env'\n"
     "if ((Test-Path $arqEnv) -and -not (Select-String -Path $arqEnv -Pattern '^\\s*AMBIENTE\\s*=\\s*producao' -Quiet)) {\n"
     "    Dizer '     ATENCAO: modo desenvolvimento (a pagina /docs fica aberta no endereco publico). Veja o LEIA-ME do passo 28.' 'Yellow'\n"
     "}\n"),
], "modo desenvolvimento (a pagina /docs")

print("\nPronto. Proximo: docker compose up -d --build   e depois   docker compose exec api pytest -q")
