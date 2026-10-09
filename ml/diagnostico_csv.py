"""Mostra como o CSV do SINAN codifica o desfecho e os sintomas (so contagens, nenhum dado de pessoa).
Uso:  python ml/diagnostico_csv.py "C:\\caminho\\DENGBR23.csv"
"""
import sys

import pandas as pd

caminho = sys.argv[1]
bruto = open(caminho, "rb").read(1 << 20)
try:
    bruto.decode("utf-8"); enc = "utf-8"
except UnicodeDecodeError as e:
    enc = "utf-8" if e.start > len(bruto) - 4 else "latin-1"
sep = ";" if bruto.decode(enc, errors="ignore").splitlines()[0].count(";") > bruto.decode(enc, errors="ignore").splitlines()[0].count(",") else ","
cols = ["CLASSI_FIN", "CRITERIO", "FEBRE", "MIALGIA", "CEFALEIA", "EXANTEMA", "DT_NOTIFIC", "SG_UF_NOT"]
cab = list(pd.read_csv(caminho, nrows=0, sep=sep, encoding=enc).columns)
usar = [c for c in cols if c in cab]
df = pd.read_csv(caminho, sep=sep, encoding=enc, usecols=usar, dtype=str)
print("separador:", repr(sep), "| codificacao:", enc, "| linhas:", f"{len(df):,}")
for c in ["CLASSI_FIN", "CRITERIO", "FEBRE", "MIALGIA", "SG_UF_NOT"]:
    if c in df:
        print(f"\n== {c} ==")
        print(df[c].value_counts(dropna=False).head(15).to_string())
if "CLASSI_FIN" in df and "CRITERIO" in df:
    print("\n== CLASSI_FIN x CRITERIO ==")
    print(pd.crosstab(df["CLASSI_FIN"].fillna("(vazio)"), df["CRITERIO"].fillna("(vazio)")).to_string())
if "DT_NOTIFIC" in df:
    print("\n== DT_NOTIFIC (primeiras e ultimas) ==")
    d = df["DT_NOTIFIC"].dropna()
    print(d.min(), "->", d.max())
