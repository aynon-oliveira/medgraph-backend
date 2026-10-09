"""Compara o modelo de gravidade COM e SEM "dias desde o inicio dos sintomas" (DT_NOTIFIC - DT_SIN_PRI).

E so um EXPERIMENTO: nao mexe em app/ia/modelo_risco.json nem no site. Usa a mesma base, a mesma divisao no tempo
e o mesmo teste do treino oficial. Serve para decidir se vale pedir essa informacao ao ACS.

COMO RODAR (com o ambiente do ML ativo, o mesmo do treino):
    python ml/comparar_dias_sintomas.py --csv caminho/DENGBR24.csv

Cuidado ao interpretar: nos dados do SINAN, quem demora mais para ser notificado tambem tende a ser mais grave por
motivos que nao sao so clinicos (busca tardia de servico). Se o ganho for pequeno, nao vale mudar o aplicativo.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
import treinar_risco as t  # noqa: E402

# o treino oficial nao le DT_SIN_PRI; aqui pedimos essa coluna a mais, sem alterar o treino oficial
_resolver_original = t.resolver_colunas
t.resolver_colunas = lambda cab, precisa, opcionais: _resolver_original(cab, precisa, list(opcionais) + ["DT_SIN_PRI"])

MAX_DIAS = 14   # acima disso e quase sempre erro de digitacao ou notificacao muito tardia


def dias_desde_inicio(df: pd.DataFrame) -> pd.Series:
    ini = pd.to_datetime(df["DT_SIN_PRI"], errors="coerce", format="mixed")
    notif = pd.to_datetime(df["DT_NOTIFIC"], errors="coerce", format="mixed")
    d = (notif - ini).dt.days.astype("float64")
    d[(d < 0) | (d > MAX_DIAS)] = np.nan
    return d


def treinar(X, y, i_tr, i_va, i_te):
    melhor = None
    for c in (0.01, 0.1, 1.0, 10.0):
        m = LogisticRegression(C=c, max_iter=2000, random_state=t.SEMENTE).fit(X[i_tr], y[i_tr])
        auc = roc_auc_score(y[i_va], m.predict_proba(X[i_va])[:, 1])
        if melhor is None or auc > melhor[0]:
            melhor = (auc, c, m)
    p = melhor[2].predict_proba(X[i_te])[:, 1]
    lim = t.limiar_por_sensibilidade(y[i_va], melhor[2].predict_proba(X[i_va])[:, 1])
    return p, lim, melhor[1], melhor[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--max-linhas", type=int, default=None)
    ap.add_argument("--saida", default="ml/comparacao_dias_sintomas.json")
    a = ap.parse_args()
    caminho = Path(a.csv)
    if "SINTETICO" in caminho.name.upper():
        sys.exit("ERRO: dados sinteticos nao servem para resultados.")

    df, sintomas, fatores, lidas = t.ler_csv(caminho, a.max_linhas, False, "todos", "gravidade")
    if "DT_SIN_PRI" not in df.columns or "DT_NOTIFIC" not in df.columns:
        sys.exit("ERRO: o CSV nao tem DT_SIN_PRI e DT_NOTIFIC. Sem as duas datas nao ha como calcular os dias.")
    x = t.montar_matriz(df, sintomas, fatores)
    y = df["alvo"].astype(int).values
    i_tr, i_va, i_te, tipo = t.dividir(df, "temporal")

    mediana_idade = float(np.nanmedian(x["idade_anos"].values[i_tr]))
    x["idade_anos"] = x["idade_anos"].fillna(mediana_idade)
    m_i, d_i = float(x["idade_anos"].values[i_tr].mean()), float(x["idade_anos"].values[i_tr].std()) or 1.0
    x["idade_z"] = (x["idade_anos"] - m_i) / d_i
    base_cols = [c for c in x.columns if c != "idade_anos"]

    dias = dias_desde_inicio(df)
    faltando = float(dias.isna().mean())
    mediana_dias = float(np.nanmedian(dias.values[i_tr]))
    dias = dias.fillna(mediana_dias)
    m_d, d_d = float(dias.values[i_tr].mean()), float(dias.values[i_tr].std()) or 1.0
    x["dias_z"] = (dias - m_d) / d_d

    print(f"casos: {len(y):,} | divisao {tipo} | dias validos: {1 - faltando:.1%} | mediana de dias: {mediana_dias:.0f}")
    p0, l0, c0, _ = treinar(x[base_cols].values.astype(float), y, i_tr, i_va, i_te)
    p1, l1, c1, m1 = treinar(x[base_cols + ["dias_z"]].values.astype(float), y, i_tr, i_va, i_te)

    yt = y[i_te]
    r0 = {**t.metricas(yt, p0, l0), **t.ic_bootstrap(yt, p0, l0)}
    r1 = {**t.metricas(yt, p1, l1), **t.ic_bootstrap(yt, p1, l1)}
    coef_dias = float(m1.coef_[0][-1])
    resultado = {
        "sem_dias": r0, "com_dias": r1, "razao_de_chances_por_desvio_padrao_de_dias": round(float(np.exp(coef_dias)), 3),
        "dias_media": round(m_d, 2), "dias_desvio": round(d_d, 2), "dias_mediana_imputada": mediana_dias,
        "proporcao_dias_ausentes_ou_invalidos": round(faltando, 4), "casos": int(len(y)), "teste": int(len(yt)),
    }
    Path(a.saida).parent.mkdir(parents=True, exist_ok=True)
    Path(a.saida).write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== TESTE (mesmo conjunto, usado so aqui) ===")
    for nome, r in (("SEM dias", r0), ("COM dias", r1)):
        print(f"{nome}: ROC-AUC {r['roc_auc']} (IC95% {r['roc_auc_ic95']}) | sens {r['sensibilidade']} | espec {r['especificidade']} | "
              f"precisao {r['precisao']} | F1 {r['f1']} | Brier {r['brier']}")
    ganho = r1["roc_auc"] - r0["roc_auc"]
    print(f"\nGanho de ROC-AUC: {ganho:+.4f} | razao de chances por 1 desvio-padrao de dias: {resultado['razao_de_chances_por_desvio_padrao_de_dias']}")
    if ganho < 0.01:
        print("LEITURA: ganho pequeno (< 0,01). Nao compensa pedir esse dado no aplicativo; cite como limitacao/trabalho futuro.")
    elif ganho < 0.03:
        print("LEITURA: ganho modesto. So vale se for facil coletar; o artigo deve dizer que continua uma discriminacao fraca.")
    else:
        print("LEITURA: ganho relevante. Vale pedir 'ha quantos dias comecaram os sintomas' no formulario do ACS (me avise).")
    print(f"Resultado gravado em {a.saida}")


if __name__ == "__main__":
    main()
