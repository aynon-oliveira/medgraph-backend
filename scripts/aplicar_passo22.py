"""Aplica o passo 22 nos arquivos que já existem (main.py, modelo Atendimento, auditoria, Dockerfile, compose).

Uso (na pasta do back-end):   python scripts/aplicar_passo22.py
Pode rodar mais de uma vez: o que já foi aplicado é ignorado. Guarda cópia dos originais em *.bak-passo22.
"""
import re
import shutil
import sys
from pathlib import Path

RAIZ = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
avisos: list[str] = []


def editar(rel: str, funcao):
    caminho = RAIZ / rel
    if not caminho.exists():
        avisos.append(f"NÃO ENCONTRADO: {rel}")
        return
    original = caminho.read_text(encoding="utf-8")
    novo = funcao(original)
    if novo is None:
        avisos.append(f"NÃO APLICADO (trecho de referência não achado): {rel}")
    elif novo == original:
        print(f"  já estava aplicado: {rel}")
    else:
        bak = caminho.with_name(caminho.name + ".bak-passo22")
        if not bak.exists():
            shutil.copy2(caminho, bak)
        caminho.write_text(novo, encoding="utf-8", newline="\n")
        print(f"  alterado: {rel}")


def main_py(s: str):
    if "app.include_router(ia.router)" in s and "modelo_versao" in s:
        return s
    m = re.search(r"from app\.routers import ([^\n]+)", s)
    if not m or "app.include_router(health.router)" not in s or "# 3)" not in s:
        return None
    nomes = [n.strip() for n in m.group(1).split(",")]
    for n in ("ia", "transcricao"):
        if n not in nomes:
            nomes.append(n)
    s = s.replace(m.group(0), "from app.routers import " + ", ".join(sorted(nomes)), 1)
    s = s.replace("app.include_router(health.router)",
                  "app.include_router(ia.router)\napp.include_router(transcricao.router)\napp.include_router(health.router)", 1)
    bloco = ('    with engine.begin() as conn:\n'
             '        conn.execute(text("ALTER TABLE atendimentos ADD COLUMN IF NOT EXISTS transcricao_audio TEXT"))\n'
             '        conn.execute(text("ALTER TABLE resultados_inferencia ADD COLUMN IF NOT EXISTS modelo_versao VARCHAR(60)"))\n\n')
    s = s.replace("    # 3)", bloco + "    # 3)", 1)
    return s


def modelo_atendimento(s: str):
    if "transcricao_audio" in s:
        return s
    m = re.search(r"^(\s*)relato_voz_path:[^\n]*\n", s, re.M)
    if not m:
        return None
    linha = f'{m.group(1)}transcricao_audio: Mapped[str | None] = mapped_column(Text, nullable=True)  # rascunho do Whisper\n'
    return s[: m.end()] + linha + s[m.end():]


def auditoria(s: str):
    if "TRANSCREVER_AUDIO" in s:
        return s
    if "VER_AUDITORIA = " not in s or "ACOES = (" not in s:
        return None
    s = s.replace('VER_AUDITORIA = "VER_AUDITORIA"', 'VER_AUDITORIA = "VER_AUDITORIA"\nTRANSCREVER_AUDIO = "TRANSCREVER_AUDIO"', 1)
    return s.replace("ACOES = (", "ACOES = (TRANSCREVER_AUDIO, ", 1)


def dockerfile(s: str):
    if "INSTALAR_WHISPER" in s:
        return s
    ancora = "RUN pip install --no-cache-dir -r requirements.txt\n"
    if ancora not in s:
        return None
    extra = ("\n# Transcrição de áudio (Whisper). Para pular: INSTALAR_WHISPER=0 no .env (imagem menor)\n"
             "ARG INSTALAR_WHISPER=1\nCOPY requirements-ia.txt .\n"
             'RUN if [ "$INSTALAR_WHISPER" = "1" ]; then pip install --no-cache-dir -r requirements-ia.txt; fi\n')
    return s.replace(ancora, ancora + extra, 1)


def compose(s: str):
    if "whisper_cache" in s:
        return s
    if "    build: .\n" not in s or "  uploads:" not in s or "- uploads:/code/uploads" not in s:
        return None
    s = s.replace("    build: .\n",
                  "    build:\n      context: .\n      args:\n        INSTALAR_WHISPER: ${INSTALAR_WHISPER:-1}\n", 1)
    s = re.sub(r"(\n\s*- uploads:/code/uploads[^\n]*)", r"\1\n      - whisper_cache:/root/.cache/huggingface   # modelo do Whisper (baixa uma vez)", s, count=1)
    s = re.sub(r"(\nvolumes:\n(?:  [^\n]*\n)*?  uploads:[^\n]*)", r"\1\n  whisper_cache:", s, count=1)
    return s


def schema_atendimento(s: str):
    if "modelo_versao" in s:
        return s
    a = "    recomendacao: str | None = None\n"
    if "class ResultadoInferenciaIn" not in s or a not in s:
        return None
    return s.replace(a, a + "    modelo_versao: str | None = Field(default=None, max_length=60)  # qual modelo/regra gerou o risco\n", 1)


def modelo_resultado(s: str):
    if "modelo_versao" in s:
        return s
    m = re.search(r"^(\s*)recomendacao:[^\n]*\n", s, re.M)
    if not m:
        return None
    return s[: m.end()] + f"{m.group(1)}modelo_versao: Mapped[str | None] = mapped_column(String(60), nullable=True)\n" + s[m.end():]


def gravar_versao(s: str):
    if "modelo_versao" in s:
        return s
    n = len(re.findall(r"recomendacao=dados\.resultado\.recomendacao,", s))
    if n == 0:
        return None
    s = re.sub(r"^(\s*)recomendacao=dados\.resultado\.recomendacao,\n",
               lambda m: m.group(0) + f"{m.group(1)}modelo_versao=dados.resultado.modelo_versao,\n", s, flags=re.M)
    s = re.sub(r"^(\s*)atendimento\.resultado\.recomendacao = dados\.resultado\.recomendacao\n",
               lambda m: m.group(0) + f"{m.group(1)}atendimento.resultado.modelo_versao = dados.resultado.modelo_versao\n", s, flags=re.M)
    return s


print(f"Aplicando o passo 22 em: {RAIZ}")
editar("app/main.py", main_py)
editar("app/models/atendimento.py", modelo_atendimento)
editar("app/services/auditoria_service.py", auditoria)
editar("app/schemas/atendimento.py", schema_atendimento)
editar("app/models/resultado_inferencia.py", modelo_resultado)
editar("app/routers/atendimentos.py", gravar_versao)
editar("app/routers/sincronizacao.py", gravar_versao)
editar("Dockerfile", dockerfile)
editar("docker-compose.yml", compose)
if avisos:
    print("\nATENÇÃO:")
    for a in avisos:
        print("  -", a)
    sys.exit(1)
print("\nPronto. Agora: docker compose up -d --build")
