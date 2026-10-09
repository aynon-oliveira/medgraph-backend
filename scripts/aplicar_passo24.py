"""Aplica o passo 24 (assistente de IA com Gemini): registra a rota no main.py e a ação de auditoria.

Uso (na pasta do back-end, com o Python do ambiente):   python scripts/aplicar_passo24.py
Pode rodar mais de uma vez. Guarda cópia dos originais em *.bak-passo24.
Não mexe no banco de dados (nenhuma tabela nova).
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
        bak = caminho.with_name(caminho.name + ".bak-passo24")
        if not bak.exists():
            shutil.copy2(caminho, bak)
        caminho.write_text(novo, encoding="utf-8", newline="\n")
        print(f"  alterado: {rel}")


def main_py(s: str):
    if "app.include_router(assistente.router)" in s:
        return s
    m = re.search(r"from app\.routers import ([^\n]+)", s)
    if not m or "app.include_router(health.router)" not in s:
        return None
    nomes = [n.strip() for n in m.group(1).split(",")]
    if "assistente" not in nomes:
        nomes.append("assistente")
    s = s.replace(m.group(0), "from app.routers import " + ", ".join(sorted(nomes)), 1)
    return s.replace("app.include_router(health.router)",
                     "app.include_router(assistente.router)\napp.include_router(health.router)", 1)


def auditoria(s: str):
    if "USAR_ASSISTENTE_IA" in s:
        return s
    if "VER_AUDITORIA = " not in s or "ACOES = (" not in s:
        return None
    s = s.replace('VER_AUDITORIA = "VER_AUDITORIA"', 'VER_AUDITORIA = "VER_AUDITORIA"\nUSAR_ASSISTENTE_IA = "USAR_ASSISTENTE_IA"', 1)
    return s.replace("ACOES = (", "ACOES = (USAR_ASSISTENTE_IA, ", 1)


def env_exemplo(s: str):
    if "GEMINI_API_KEY" in s:
        return s
    return s.rstrip("\n") + (
        "\n\n# Assistente de IA (Gemini / Google AI Studio). Deixe a chave vazia para desligar.\n"
        "GEMINI_API_KEY=\nGEMINI_ATIVO=1\nGEMINI_MODELO=gemini-flash-lite-latest\nGEMINI_MODELO_RESERVA=gemini-flash-latest\nGEMINI_LIMITE_HORA=30\nWHISPER_MODELO=base\n")


print(f"Aplicando o passo 24 em: {RAIZ}")
editar("app/main.py", main_py)
editar("app/services/auditoria_service.py", auditoria)
if (RAIZ / ".env.example").exists():
    editar(".env.example", env_exemplo)
if avisos:
    print("\nATENÇÃO:")
    for a in avisos:
        print("  -", a)
    sys.exit(1)
print("\nPronto. Agora coloque a chave no .env e rode: docker compose up -d --force-recreate api")
