"""Passo 26: campo "dias desde o início dos sintomas" (opcional) no atendimento.

Uso (na pasta do back-end):   python scripts/aplicar_passo26.py
Pode rodar mais de uma vez. Guarda cópia dos originais em *.bak-passo26.
Se algum arquivo não tiver o trecho de referência, NADA é gravado nele e o aviso aparece no fim.
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
    try:
        novo = funcao(original)
    except Exception as erro:  # noqa: BLE001
        avisos.append(f"NÃO APLICADO ({erro}): {rel}")
        return
    if novo == original:
        print(f"  já estava aplicado: {rel}")
        return
    bak = caminho.with_name(caminho.name + ".bak-passo26")
    if not bak.exists():
        shutil.copy2(caminho, bak)
    caminho.write_text(novo, encoding="utf-8", newline="\n")
    print(f"  aplicado: {rel}")


def uma_vez(texto: str, antigo: str, novo: str, marca: str) -> str:
    """Troca `antigo` por `novo` (uma única ocorrência). Se `marca` já está no texto, não faz nada."""
    if marca in texto:
        return texto
    if texto.count(antigo) != 1:
        raise ValueError(f"trecho de referência achado {texto.count(antigo)}x (esperado 1): {antigo.strip()[:60]!r}")
    return texto.replace(antigo, novo)


def apos_linha(texto: str, padrao: str, linha_nova: str, marca: str, quantas: int = 1) -> str:
    """Insere `linha_nova` logo depois da linha que casa com `padrao`, com a mesma indentação."""
    if marca in texto:
        return texto
    regex = re.compile(r"^([ \t]*)" + padrao + r"[ \t]*\n", re.M)
    achados = list(regex.finditer(texto))
    if len(achados) != quantas:
        raise ValueError(f"linha de referência achada {len(achados)}x (esperado {quantas}): {padrao[:50]!r}")
    for m in reversed(achados):
        texto = texto[: m.end()] + m.group(1) + linha_nova + "\n" + texto[m.end():]
    return texto


DIAS_CAMPO = 'dias_sintomas: int | None = Field(default=None, ge=0, le=30)  # há quantos dias começaram os sintomas'


def modelo_atendimento(t):
    t = uma_vez(t, "from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, Text",
                "from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text", "ForeignKey, Integer")
    return apos_linha(t, r"sintomas: Mapped\[list\[str\]\] = mapped_column\(ARRAY\(String\).*",
                      "# Há quantos dias começaram os sintomas (0 a 30; opcional). Entra no modelo de risco.\n"
                      "    dias_sintomas: Mapped[int | None] = mapped_column(Integer, nullable=True)", "dias_sintomas")


def schema_atendimento(t):
    # AtendimentoCreate (linha com max_length=30) e AtendimentoOut (sem max_length)
    t = apos_linha(t, r"sintomas: list\[str\] = Field\(default_factory=list, max_length=30\)", DIAS_CAMPO, "dias_sintomas")
    return apos_linha(t, r"sintomas: list\[str\] = Field\(default_factory=list\)",
                      "dias_sintomas: int | None = None", "dias_sintomas: int | None = None")


def schema_sync(t):
    return apos_linha(t, r"sintomas: list\[str\] = Field\(default_factory=list, max_length=30\)", DIAS_CAMPO, "dias_sintomas")


def rota_sync(t):
    t = apos_linha(t, r"sintomas=dados\.sintomas,", "dias_sintomas=dados.dias_sintomas,", "dias_sintomas=dados")
    return apos_linha(t, r"existente\.sintomas = dados\.sintomas", "existente.dias_sintomas = dados.dias_sintomas", "existente.dias_sintomas")


def rota_atendimentos(t):
    # saída (AtendimentoOut) e criação (Atendimento(...)): cada uma tem a sua linha
    if "dias_sintomas" in t:
        return t
    t = apos_linha(t, r"sintomas=list\(a\.sintomas or \[\]\),", "dias_sintomas=a.dias_sintomas,", "XXXX_nunca")
    return apos_linha(t, r"sintomas=dados\.sintomas,", "dias_sintomas=dados.dias_sintomas,", "XXXX_nunca")


def main_py(t):
    marca = "ADD COLUMN IF NOT EXISTS dias_sintomas"
    bloco = ('    with engine.begin() as conn:\n'
             '        conn.execute(text("ALTER TABLE atendimentos ADD COLUMN IF NOT EXISTS dias_sintomas INTEGER"))\n\n')
    return uma_vez(t, "    # 3) Restrições de unicidade do grafo", bloco + "    # 3) Restrições de unicidade do grafo", marca)


NOVA_PONTUAR = '''def pontuar(modelo: dict, sintomas: list[str], idade_anos: float | None = None, sexo: str | None = None,
            dias_sintomas: float | None = None) -> float:
    """Probabilidade estimada (0 a 1). Sintoma não informado conta como ausente; idade e dias ausentes usam o valor típico."""
    marcadas, _ = sintomas_para_variaveis(modelo, sintomas)
    z = float(modelo["intercepto"])
    for f in modelo["features"]:
        nome = f["nome"]
        if f.get("tipo") == "numerica" and nome == "dias_sintomas":
            padrao = modelo.get("dias_padrao", f["media"])
            dias = min(dias_sintomas, 14) if dias_sintomas is not None and 0 <= dias_sintomas <= 30 else padrao   # o treino limita a 14
            z += f["coef"] * ((dias - f["media"]) / f["desvio"])
        elif f.get("tipo") == "numerica":  # idade
            idade = idade_anos if idade_anos is not None and 0 <= idade_anos <= 110 else modelo.get("idade_padrao_anos", f["media"])
            z += f["coef"] * ((idade - f["media"]) / f["desvio"])
        elif nome == "sexo_f":
            z += f["coef"] * (1.0 if (sexo or "").upper() == "F" else 0.0)
        else:
            z += f["coef"] * (1.0 if nome in marcadas else 0.0)
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


'''


def ia_risco(t):
    if "dias_sintomas" in t:
        return t
    i, j = t.find("def pontuar("), t.find("def informacao_publica(")
    if i < 0 or j < 0 or j < i:
        raise ValueError("função pontuar não encontrada")
    return t[:i] + NOVA_PONTUAR + t[j:]


# ---------- treino (ml/treinar_risco.py): opção --dias-sintomas ----------
def treino(t):
    if "dias-sintomas" in t:
        return t
    t = uma_vez(t, '["CS_SEXO", "NU_IDADE_N", "DT_NOTIFIC", "SG_UF_NOT"] + fatores_opc)',
                '["CS_SEXO", "NU_IDADE_N", "DT_NOTIFIC", "DT_SIN_PRI", "SG_UF_NOT"] + fatores_opc)', "DT_SIN_PRI")
    t = apos_linha(t, r'ap\.add_argument\("--incluir-exames".*\)',
                   'ap.add_argument("--dias-sintomas", action="store_true", help="inclui os dias desde o inicio dos sintomas (DT_NOTIFIC - DT_SIN_PRI, limitado a 14)")',
                   "--dias-sintomas")
    bloco = '''    mediana_dias = 2.0
    if args.dias_sintomas:
        if "DT_SIN_PRI" not in df.columns or "DT_NOTIFIC" not in df.columns:
            sys.exit("ERRO: --dias-sintomas precisa das colunas DT_SIN_PRI e DT_NOTIFIC no CSV.")
        _ini = pd.to_datetime(df["DT_SIN_PRI"], errors="coerce", format="mixed")
        _not = pd.to_datetime(df["DT_NOTIFIC"], errors="coerce", format="mixed")
        _dias = (_not - _ini).dt.days.astype("float64")
        _dias[(_dias < 0) | (_dias > 14)] = np.nan
        mediana_dias = float(np.nanmedian(_dias.values[i_tr]))
        _dias = _dias.fillna(mediana_dias)
        media_dias = float(_dias.values[i_tr].mean())
        dp_dias = float(_dias.values[i_tr].std()) or 1.0
        x["dias_z"] = (_dias - media_dias) / dp_dias
'''
    t = uma_vez(t, '    x["idade_z"] = (x["idade_anos"] - media_idade) / dp_idade\n',
                '    x["idade_z"] = (x["idade_anos"] - media_idade) / dp_idade\n' + bloco, "mediana_dias = 2.0")
    t = uma_vez(t, '        elif nome == "sexo_f":\n            item.update({"tipo": "binaria", "rotulo": "Sexo feminino"})',
                '        elif nome == "dias_z":\n'
                '            item.update({"nome": "dias_sintomas", "tipo": "numerica", "rotulo": "Dias desde o início dos sintomas",\n'
                '                         "media": round(media_dias, 4), "desvio": round(dp_dias, 4)})\n'
                '        elif nome == "sexo_f":\n            item.update({"tipo": "binaria", "rotulo": "Sexo feminino"})', 'nome == "dias_z"')
    t = apos_linha(t, r'"idade_padrao_anos": round\(mediana, 2\),',
                   '**({"dias_padrao": round(mediana_dias, 2), "dias_max": 14} if args.dias_sintomas else {}),', "dias_padrao")
    t = uma_vez(t, '"versao": alvo["prefixo"] + agora.strftime("%Y%m%d"),',
                '"versao": alvo["prefixo"] + agora.strftime("%Y%m%d") + ("-dias" if args.dias_sintomas else ""),', '"-dias"')
    return t


print("Aplicando o passo 26 em", RAIZ)
editar("app/models/atendimento.py", modelo_atendimento)
editar("app/schemas/atendimento.py", schema_atendimento)
editar("app/schemas/sincronizacao.py", schema_sync)
editar("app/routers/sincronizacao.py", rota_sync)
editar("app/routers/atendimentos.py", rota_atendimentos)
editar("app/main.py", main_py)
editar("app/services/ia_risco.py", ia_risco)
editar("ml/treinar_risco.py", treino)
if avisos:
    print("\nATENÇÃO:")
    for a in avisos:
        print("  -", a)
    print("Me mande esta tela. Os arquivos citados acima NÃO foram alterados.")
    sys.exit(1)
print("\nPronto. Agora: docker compose up -d --force-recreate api  e depois  docker compose exec api pytest -v")
