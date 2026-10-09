"""Treina o classificador de dengue do MEDGRAPH-AM com dados reais do SINAN e exporta o modelo em JSON.

O QUE ELE FAZ
  1. Le o CSV de notificacoes de dengue do SINAN (dados abertos, sem nomes) so com as colunas necessarias.
  2. Monta o alvo. Padrao (--alvo gravidade): dengue COM SINAIS DE ALARME/GRAVE (CLASSI_FIN 11, 12) x dengue SEM sinais de
     alarme (CLASSI_FIN 10). (--alvo dengue: confirmada x descartada, so se o arquivo tiver CLASSI_FIN 5.)
  3. Divide os dados no TEMPO (treino = notificacoes mais antigas, validacao e teste = mais novas).
     O teste so e usado UMA vez, no fim.
  4. Treina uma regressao logistica (interpretavel e exportavel para JSON) e compara com dois pontos de referencia:
     o chute pela taxa de dengue e um modelo de arvores (so para comparar, nao e exportado).
  5. Escolhe o limiar na validacao, mede no teste e grava:
       app/ia/modelo_risco.json         -> o modelo usado pela API e pelo site
       ml/relatorio_metricas.json / .md -> numeros para o artigo

COMO RODAR (veja ml/LEIA-ME-IA.md):
    python ml/treinar_risco.py --csv caminho/DENGBR24.csv

Somente as metricas MEDIDAS aqui podem ir para o artigo. Nunca use dados sinteticos nos resultados.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

# Sintomas do SINAN (campo 33 da ficha): 1 = Sim, 2 = Nao. Nome no SINAN -> (nome no modelo, rotulo na tela)
SINTOMAS = {
    "FEBRE": ("febre", "Febre"),
    "MIALGIA": ("mialgia", "Dor muscular"),
    "CEFALEIA": ("cefaleia", "Dor de cabeça"),
    "EXANTEMA": ("exantema", "Manchas na pele (exantema)"),
    "VOMITO": ("vomito", "Vômito"),
    "NAUSEA": ("nausea", "Náusea"),
    "DOR_COSTAS": ("dor_costas", "Dor nas costas"),
    "CONJUNTVIT": ("conjuntivite", "Conjuntivite"),
    "ARTRITE": ("artrite", "Artrite"),
    "ARTRALGIA": ("artralgia", "Dor intensa nas articulações"),
    "PETEQUIA_N": ("petequias", "Petéquias"),
    "LEUCOPENIA": ("leucopenia", "Leucopenia (exame)"),
    "LACO": ("prova_laco", "Prova do laço positiva"),
    "DOR_RETRO": ("dor_retro_orbital", "Dor atrás dos olhos"),
}
# Leucopenia e prova do laco dependem de exame/procedimento: ficam fora do modelo por padrao,
# porque o ACS nao tem esses dados no atendimento em campo (use --incluir-exames para testar).
SO_EXAMES = {"LEUCOPENIA", "LACO"}
# Fatores de risco/comorbidades da ficha (1 = sim). Entram so no alvo "gravidade".
FATORES = {
    "DIABETES": ("diabetes", "Diabetes"),
    "HEMATOLOG": ("doenca_hematologica", "Doença hematológica"),
    "HEPATOPAT": ("hepatopatia", "Hepatopatia"),
    "RENAL": ("doenca_renal", "Doença renal crônica"),
    "HIPERTENSA": ("hipertensao", "Hipertensão arterial"),
    "ACIDO_PEPT": ("doenca_acido_peptica", "Doença ácido-péptica"),
    "AUTO_IMUNE": ("doenca_autoimune", "Doença autoimune"),
}
# Alvos possiveis. A base ABERTA do SINAN (dengue) traz so casos provaveis (sem CLASSI_FIN = 5, descartado),
# por isso o alvo padrao e "gravidade": dengue com sinais de alarme/grave (11, 12) x dengue sem sinais de alarme (10).
ALVOS = {
    "gravidade": {
        "positivas": {11, 12}, "negativas": {10}, "criterio_padrao": "todos", "prefixo": "risco-gravidade-lr-",
        "texto": "evolucao com sinais de alarme ou dengue grave (CLASSI_FIN 11/12) x dengue sem sinais de alarme (CLASSI_FIN 10), entre casos provaveis de dengue notificados",
        "rotulo_probabilidade": "chance estimada de evoluir com sinais de alarme ou dengue grave",
        "titulo": "classificador de risco de gravidade na dengue",
    },
    "dengue": {
        "positivas": {10, 11, 12}, "negativas": {5}, "criterio_padrao": "laboratorio", "prefixo": "risco-dengue-lr-",
        "texto": "dengue confirmada (CLASSI_FIN 10/11/12) x caso descartado (CLASSI_FIN 5)",
        "rotulo_probabilidade": "probabilidade estimada de dengue",
        "titulo": "classificador de dengue",
    },
}
SEMENTE = 42


def sem_acento(texto: str) -> str:
    t = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in t if not unicodedata.combining(c)).upper().strip()


def resolver_colunas(cabecalho: list[str], precisa: list[str], opcionais: list[str]) -> dict[str, str]:
    """Acha cada coluna do SINAN no arquivo, ignorando acento/maiuscula e nomes cortados em 10 letras."""
    norm = {sem_acento(c): c for c in cabecalho}
    achadas, faltando = {}, []
    for nome in precisa + opcionais:
        candidatos = [nome, nome[:10], nome.replace("_N", "")]
        achou = next((norm[c] for c in candidatos if c in norm), None)
        if achou is None:
            (faltando if nome in precisa else []).append(nome)
        else:
            achadas[nome] = achou
    if faltando:
        sys.exit(
            "ERRO: colunas obrigatorias nao encontradas no CSV: " + ", ".join(faltando)
            + "\nColunas do arquivo (primeiras 40): " + ", ".join(cabecalho[:40])
        )
    return achadas


def idade_em_anos(codigo: pd.Series) -> pd.Series:
    """NU_IDADE_N: 1o digito = unidade (1 hora, 2 dia, 3 mes, 4 ano) e o resto = valor. Ex.: 4013 = 13 anos."""
    c = pd.to_numeric(codigo, errors="coerce")
    unidade = (c // 1000).astype("Int64")
    valor = (c % 1000).astype("Float64")
    anos = pd.Series(np.nan, index=c.index, dtype="float64")
    anos[unidade == 4] = valor[unidade == 4].astype("float64")
    anos[unidade == 3] = (valor[unidade == 3] / 12.0).astype("float64")
    anos[unidade == 2] = (valor[unidade == 2] / 365.0).astype("float64")
    anos[unidade == 1] = 0.0
    anos[(anos < 0) | (anos > 110)] = np.nan
    return anos


def ler_csv(caminho: Path, max_linhas: int | None, incluir_exames: bool, criterio: str, alvo: str):
    """Le o CSV em pedacos (arquivos do SINAN chegam a varios GB) e ja filtra e converte."""
    bruto = open(caminho, "rb").read(1 << 20)
    try:
        bruto.decode("utf-8")
        enc = "utf-8"
    except UnicodeDecodeError as e:
        # um caractere cortado no fim do bloco nao conta como erro
        enc = "utf-8" if e.start > len(bruto) - 4 else "latin-1"
    primeira = bruto.decode(enc, errors="ignore").splitlines()[0]
    sep = ";" if primeira.count(";") > primeira.count(",") else ","
    cab = list(pd.read_csv(caminho, nrows=0, sep=sep, encoding=enc).columns)
    sintomas = [s for s in SINTOMAS if incluir_exames or s not in SO_EXAMES]
    fatores_opc = list(FATORES) + ["CS_GESTANT"] if alvo == "gravidade" else []
    col = resolver_colunas(cab, ["CLASSI_FIN", "CRITERIO"] + sintomas, ["CS_SEXO", "NU_IDADE_N", "DT_NOTIFIC", "DT_SIN_PRI", "SG_UF_NOT"] + fatores_opc)
    fatores = [c for c in fatores_opc if c in col]
    positivas, negativas = ALVOS[alvo]["positivas"], ALVOS[alvo]["negativas"]
    usar = list(dict.fromkeys(col.values()))
    pedacos, lidas, rotuladas = [], 0, 0
    for pedaco in pd.read_csv(caminho, sep=sep, encoding=enc, usecols=usar, dtype=str, chunksize=250_000):
        lidas += len(pedaco)
        pedaco = pedaco.rename(columns={v: k for k, v in col.items()})
        fin = pd.to_numeric(pedaco["CLASSI_FIN"], errors="coerce")
        crit = pd.to_numeric(pedaco["CRITERIO"], errors="coerce")
        ok = fin.isin(positivas | negativas)
        if criterio == "laboratorio":
            ok &= crit == 1
        pedaco = pedaco[ok].copy()
        pedaco["alvo"] = pd.to_numeric(pedaco["CLASSI_FIN"], errors="coerce").isin(positivas).astype(int)
        rotuladas += len(pedaco)
        pedacos.append(pedaco)
        if max_linhas and rotuladas >= max_linhas:
            break
    if not pedacos:
        sys.exit("ERRO: nenhum caso com os valores de CLASSI_FIN esperados para o alvo '" + alvo + "' foi encontrado.")
    df = pd.concat(pedacos, ignore_index=True)
    if max_linhas:
        df = df.head(max_linhas)
    return df, sintomas, fatores, lidas


def montar_matriz(df: pd.DataFrame, sintomas: list[str], fatores: list[str]) -> pd.DataFrame:
    x = pd.DataFrame(index=df.index)
    for s in sintomas:
        x[SINTOMAS[s][0]] = (pd.to_numeric(df[s], errors="coerce") == 1).astype(int)
    for fat in fatores:
        if fat == "CS_GESTANT":   # 1 a 4 = trimestre/idade gestacional ignorada; 5 = nao; 6 = nao se aplica
            x["gestante"] = pd.to_numeric(df[fat], errors="coerce").isin([1, 2, 3, 4]).astype(int)
        else:
            x[FATORES[fat][0]] = (pd.to_numeric(df[fat], errors="coerce") == 1).astype(int)
    if "NU_IDADE_N" in df.columns:
        x["idade_anos"] = idade_em_anos(df["NU_IDADE_N"])
    else:
        x["idade_anos"] = np.nan
    if "CS_SEXO" in df.columns:
        x["sexo_f"] = (df["CS_SEXO"].astype(str).str.upper().str.strip() == "F").astype(int)
    else:
        x["sexo_f"] = 0
    return x


def dividir(df: pd.DataFrame, modo: str):
    """Treino 70%, validacao 15%, teste 15%. Temporal = os mais novos ficam para validacao e teste."""
    n = len(df)
    if modo == "temporal" and "DT_NOTIFIC" in df.columns:
        datas = pd.to_datetime(df["DT_NOTIFIC"], errors="coerce", format="mixed")
        if datas.notna().mean() > 0.9:
            ordem = np.argsort(datas.fillna(datas.max()).values, kind="stable")
            tipo = "temporal"
        else:
            ordem = np.random.RandomState(SEMENTE).permutation(n)
            tipo = "aleatoria (datas ausentes)"
    else:
        ordem = np.random.RandomState(SEMENTE).permutation(n)
        tipo = "aleatoria"
    a, b = int(n * 0.70), int(n * 0.85)
    return ordem[:a], ordem[a:b], ordem[b:], tipo


def metricas(y, p, limiar):
    pred = (p >= limiar).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    return {
        "limiar": round(float(limiar), 4),
        "acuracia": round(float((tp + tn) / len(y)), 4),
        "precisao": round(float(precision_score(y, pred, zero_division=0)), 4),
        "sensibilidade": round(float(recall_score(y, pred, zero_division=0)), 4),
        "especificidade": round(float(tn / (tn + fp)) if (tn + fp) else 0.0, 4),
        "f1": round(float(f1_score(y, pred, zero_division=0)), 4),
        "roc_auc": round(float(roc_auc_score(y, p)), 4),
        "pr_auc": round(float(average_precision_score(y, p)), 4),
        "brier": round(float(brier_score_loss(y, p)), 4),
        "matriz_confusao": {"vn": int(tn), "fp": int(fp), "fn": int(fn), "vp": int(tp)},
    }


def melhor_limiar(y, p):
    melhor, lim = -1.0, 0.5
    for t in np.linspace(0.05, 0.95, 91):
        f = f1_score(y, (p >= t).astype(int), zero_division=0)
        if f > melhor:
            melhor, lim = f, float(t)
    return lim


def limiar_por_sensibilidade(y, p, alvo_sens=0.80):
    """Maior limiar que ainda acha pelo menos 80% dos casos graves na validacao (triagem: errar para o lado seguro)."""
    y = np.asarray(y); p = np.asarray(p)
    total = y.sum()
    melhor = float(p.min())
    for t in np.unique(np.quantile(p, np.linspace(0, 1, 2001))):
        if (p[y == 1] >= t).sum() / total >= alvo_sens:
            melhor = float(t)
        else:
            break
    return melhor


def ic_bootstrap(y, p, limiar, repeticoes=1000):
    """Intervalo de 95% por reamostragem (bootstrap) para ROC-AUC e F1 no teste."""
    rs = np.random.RandomState(SEMENTE)
    aucs, f1s = [], []
    y = np.asarray(y)
    p = np.asarray(p)
    for _ in range(repeticoes):
        i = rs.randint(0, len(y), len(y))
        if len(set(y[i])) < 2:
            continue
        aucs.append(roc_auc_score(y[i], p[i]))
        f1s.append(f1_score(y[i], (p[i] >= limiar).astype(int), zero_division=0))
    return {
        "roc_auc_ic95": [round(float(np.percentile(aucs, 2.5)), 4), round(float(np.percentile(aucs, 97.5)), 4)],
        "f1_ic95": [round(float(np.percentile(f1s, 2.5)), 4), round(float(np.percentile(f1s, 97.5)), 4)],
    }


def calibracao(y, p, faixas=10):
    """Calibracao por decis da probabilidade prevista (serve para taxas baixas, onde faixas fixas de 0,1 nao mostram nada)."""
    y, p = np.asarray(y), np.asarray(p)
    ordem = np.argsort(p, kind="stable")
    saida = []
    for g in np.array_split(ordem, faixas):
        if len(g):
            saida.append({"faixa": f"{p[g].min():.3f}-{p[g].max():.3f}", "n": int(len(g)),
                          "previsto": round(float(p[g].mean()), 4), "observado": round(float(y[g].mean()), 4)})
    return saida


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description="Treina o classificador de dengue (regressao logistica) com dados do SINAN.")
    ap.add_argument("--csv", required=True, help="CSV do SINAN (dengue)")
    ap.add_argument("--saida-modelo", default="app/ia/modelo_risco.json")
    ap.add_argument("--saida-relatorio", default="ml/relatorio_metricas")
    ap.add_argument("--alvo", choices=["gravidade", "dengue"], default="gravidade",
                    help="gravidade (padrao): dengue com sinais de alarme/grave x sem sinais de alarme; dengue: dengue x descartado (exige arquivo com CLASSI_FIN=5)")
    ap.add_argument("--criterio", choices=["laboratorio", "todos"], default=None,
                    help="laboratorio: so casos com CRITERIO=1; todos: inclui clinico-epidemiologico. Padrao: todos (alvo gravidade) ou laboratorio (alvo dengue)")
    ap.add_argument("--divisao", choices=["temporal", "aleatoria"], default="temporal")
    ap.add_argument("--max-linhas", type=int, default=None, help="limita casos rotulados (para testar rapido)")
    ap.add_argument("--incluir-exames", action="store_true", help="inclui leucopenia e prova do laco como variaveis")
    ap.add_argument("--dias-sintomas", action="store_true", help="inclui os dias desde o inicio dos sintomas (DT_NOTIFIC - DT_SIN_PRI, limitado a 14)")
    ap.add_argument("--permitir-sintetico", action="store_true", help="so para testar o script; NUNCA para resultados")
    args = ap.parse_args()

    alvo = ALVOS[args.alvo]
    if args.criterio is None:
        args.criterio = alvo["criterio_padrao"]
    caminho = Path(args.csv)
    sintetico = "SINTETICO" in caminho.name.upper()
    if sintetico and not args.permitir_sintetico:
        sys.exit("ERRO: o arquivo parece SINTETICO. Dados sinteticos nao servem para resultados. (Use --permitir-sintetico so para testar o script.)")

    print("Lendo", caminho, "...")
    df, sintomas, fatores, lidas = ler_csv(caminho, args.max_linhas, args.incluir_exames, args.criterio, args.alvo)
    print(f"  alvo: {args.alvo} | criterio: {args.criterio} | linhas lidas: {lidas:,} | casos rotulados usados: {len(df):,}")

    x = montar_matriz(df, sintomas, fatores)
    y = df["alvo"].astype(int).values
    if y.sum() < 50 or (len(y) - y.sum()) < 50:
        sys.exit("ERRO: poucos casos de uma das classes (menos de 50): positivos %d, negativos %d. Alvo '%s'. Rode ml/diagnostico_csv.py para ver os valores de CLASSI_FIN do arquivo." % (y.sum(), len(y) - y.sum(), args.alvo))

    i_tr, i_va, i_te, tipo_divisao = dividir(df, args.divisao)
    mediana = float(np.nanmedian(x["idade_anos"].values[i_tr])) if x["idade_anos"].notna().any() else 30.0
    x["idade_anos"] = x["idade_anos"].fillna(mediana)
    media_idade = float(x["idade_anos"].values[i_tr].mean())
    dp_idade = float(x["idade_anos"].values[i_tr].std()) or 1.0
    x["idade_z"] = (x["idade_anos"] - media_idade) / dp_idade
    mediana_dias = 2.0
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
    colunas = [c for c in x.columns if c not in ("idade_anos",)]
    X = x[colunas].values.astype(float)

    Xtr, ytr = X[i_tr], y[i_tr]
    Xva, yva = X[i_va], y[i_va]
    Xte, yte = X[i_te], y[i_te]
    print(f"  treino {len(ytr):,} | validacao {len(yva):,} | teste {len(yte):,} | divisao {tipo_divisao}")
    print(f"  taxa de dengue: treino {ytr.mean():.1%} | validacao {yva.mean():.1%} | teste {yte.mean():.1%}")

    # --- regressao logistica: escolhe C na validacao ---
    melhor = None
    for c in (0.01, 0.1, 1.0, 10.0):
        m = LogisticRegression(C=c, max_iter=2000, random_state=SEMENTE)
        m.fit(Xtr, ytr)
        auc = roc_auc_score(yva, m.predict_proba(Xva)[:, 1])
        print(f"  LR C={c:<5} AUC validacao = {auc:.4f}")
        if melhor is None or auc > melhor[0]:
            melhor = (auc, c, m)
    _, c_ok, lr = melhor
    p_va = lr.predict_proba(Xva)[:, 1]
    limiar = limiar_por_sensibilidade(yva, p_va) if args.alvo == "gravidade" else melhor_limiar(yva, p_va)
    p_te = lr.predict_proba(Xte)[:, 1]

    # --- referencias (nao exportadas) ---
    gb = HistGradientBoostingClassifier(random_state=SEMENTE, max_iter=200)
    gb.fit(Xtr, ytr)
    p_gb = gb.predict_proba(Xte)[:, 1]
    lim_gb = melhor_limiar(yva, gb.predict_proba(Xva)[:, 1])
    taxa = float(ytr.mean())
    p_base = np.full(len(yte), taxa)

    rel = {
        "regressao_logistica": {**metricas(yte, p_te, limiar), **ic_bootstrap(yte, p_te, limiar)},
        "arvores_gradient_boosting_referencia": metricas(yte, p_gb, lim_gb),
        "baseline_taxa_de_dengue": {
            "roc_auc": 0.5, "acuracia_se_sempre_positivo": round(float(yte.mean()), 4),
            "acuracia_se_sempre_negativo": round(float(1 - yte.mean()), 4),
        },
        "calibracao_teste": calibracao(yte, p_te),
    }

    # --- coeficientes ---
    feats = []
    nomes_tela = {v[0]: v[1] for v in SINTOMAS.values()}
    nomes_tela.update({v[0]: v[1] for v in FATORES.values()})
    nomes_tela["gestante"] = "Gestante"
    nomes_fator = {v[0] for v in FATORES.values()} | {"gestante"}
    for nome, coef in zip(colunas, lr.coef_[0]):
        item = {"nome": nome, "coef": round(float(coef), 6)}
        if nome == "idade_z":
            item.update({"nome": "idade_anos", "tipo": "numerica", "rotulo": "Idade (anos)",
                         "media": round(media_idade, 4), "desvio": round(dp_idade, 4)})
        elif nome == "dias_z":
            item.update({"nome": "dias_sintomas", "tipo": "numerica", "rotulo": "Dias desde o início dos sintomas",
                         "media": round(media_dias, 4), "desvio": round(dp_dias, 4)})
        elif nome == "sexo_f":
            item.update({"tipo": "binaria", "rotulo": "Sexo feminino"})
        else:
            item.update({"tipo": "binaria", "rotulo": nomes_tela.get(nome, nome), "grupo": "fator" if nome in nomes_fator else "sintoma"})
        feats.append(item)
    razoes = sorted(({"variavel": f["rotulo"], "razao_de_chances": round(float(np.exp(f["coef"])), 3)} for f in feats),
                    key=lambda d: -d["razao_de_chances"])

    agora = datetime.now(timezone.utc)
    modelo = {
        "versao": alvo["prefixo"] + agora.strftime("%Y%m%d") + ("-dias" if args.dias_sintomas else ""),
        "tipo": "regressao_logistica",
        "alvo_chave": args.alvo,
        "rotulo_probabilidade": alvo["rotulo_probabilidade"],
        "taxa_base": round(float(y.mean()), 5),
        "alvo": alvo["texto"] + (" - somente casos fechados por laboratorio" if args.criterio == "laboratorio" else " - todos os criterios"),
        "criado_em": agora.isoformat(timespec="seconds"),
        "sintetico": bool(sintetico),
        "features": feats,
        "intercepto": round(float(lr.intercept_[0]), 6),
        "idade_padrao_anos": round(mediana, 2),
        **({"dias_padrao": round(mediana_dias, 2), "dias_max": 14} if args.dias_sintomas else {}),
        "limiar": round(limiar, 4),
        "regularizacao_C": c_ok,
        "metricas_teste": {k: v for k, v in rel["regressao_logistica"].items()},
        "dados": {
            "fonte": "SINAN - Notificacoes de dengue (dados abertos do Ministerio da Saude)",
            "arquivo": caminho.name, "sha256": sha256(caminho), "linhas_lidas": int(lidas),
            "casos_usados": int(len(df)), "positivos": int(y.sum()), "negativos": int(len(y) - y.sum()),
            "treino": int(len(ytr)), "validacao": int(len(yva)), "teste": int(len(yte)), "divisao": tipo_divisao,
        },
        "avisos": [
            "Modelo de APOIO a triagem (RN01): nao substitui a avaliacao do profissional de saude.",
            "Treinado com casos notificados e investigados (com desfecho); pode nao representar todos os atendimentos em campo.",
            "O rotulo vem da classificacao final do SINAN; quando o criterio nao e laboratorial, depende do juizo clinico-epidemiologico.",
        ] + ([
            "A base aberta do SINAN so traz casos provaveis de dengue (sem descartados): este modelo NAO diz se o paciente tem dengue, so a chance de evoluir com sinais de alarme/grave ENTRE casos de dengue.",
            "O desfecho (sinais de alarme/grave) pode aparecer dias depois da notificacao; as variaveis usadas sao as conhecidas na visita (sintomas, idade, sexo, gestacao, comorbidades).",
        ] if args.alvo == "gravidade" else []) + [
            "Nao foi validado em campo nem em aparelhos reais; use a pontuacao como indicativo, nunca como diagnostico.",
        ],
    }
    if sintetico:
        modelo["avisos"].insert(0, "DADOS SINTETICOS: este modelo e so um teste do codigo e NAO tem valor clinico.")

    # Confere que o JSON (valores arredondados) reproduz as previsoes do modelo treinado
    z = np.full(len(i_te), modelo["intercepto"])
    for f, nome in zip(feats, colunas):
        v = ((x["idade_anos"].values[i_te] - f["media"]) / f["desvio"]) if nome == "idade_z" else x[nome].values[i_te]
        z = z + f["coef"] * v
    dif = float(np.max(np.abs(1 / (1 + np.exp(-z)) - p_te)))
    print(f"  conferencia do JSON exportado: diferenca maxima de probabilidade = {dif:.2e}")
    if dif > 1e-3:
        sys.exit("ERRO: o JSON exportado nao reproduz o modelo treinado. Nada foi salvo.")
    modelo["conferencia_json_dif_max"] = float(f"{dif:.3e}")

    Path(args.saida_modelo).parent.mkdir(parents=True, exist_ok=True)
    Path(args.saida_modelo).write_text(json.dumps(modelo, ensure_ascii=False, indent=2), encoding="utf-8")

    relatorio = {"gerado_em": modelo["criado_em"], "dados": modelo["dados"], "alvo": modelo["alvo"],
                 "sintetico": bool(sintetico), "titulo": alvo["titulo"], "taxa_base": modelo["taxa_base"], "resultados_teste": rel, "razoes_de_chances": razoes}
    base = Path(args.saida_relatorio)
    base.parent.mkdir(parents=True, exist_ok=True)
    base.with_suffix(".json").write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
    base.with_suffix(".md").write_text(relatorio_md(relatorio), encoding="utf-8")
    grafico(base, yte, p_te, p_gb, rel)

    r = rel["regressao_logistica"]
    print("\n=== RESULTADO NO TESTE (regressao logistica) ===")
    print(f"  ROC-AUC {r['roc_auc']} (IC95% {r['roc_auc_ic95']}) | F1 {r['f1']} (IC95% {r['f1_ic95']})")
    print(f"  acuracia {r['acuracia']} | sensibilidade {r['sensibilidade']} | especificidade {r['especificidade']} | limiar {r['limiar']}")
    print(f"  referencia (arvores): ROC-AUC {rel['arvores_gradient_boosting_referencia']['roc_auc']}")
    print(f"\nModelo salvo em {args.saida_modelo}\nRelatorio em {base.with_suffix('.md')} e {base.with_suffix('.json')}")
    if sintetico:
        print("\n*** DADOS SINTETICOS: resultado sem valor. Nao use no artigo. ***")


def relatorio_md(rel: dict) -> str:
    d, r = rel["dados"], rel["resultados_teste"]["regressao_logistica"]
    g = rel["resultados_teste"]["arvores_gradient_boosting_referencia"]
    L = ["# Relatório de métricas do " + rel.get("titulo", "classificador de dengue"), ""]
    if rel["sintetico"]:
        L += ["> **DADOS SINTÉTICOS: sem valor clínico. Não usar no artigo.**", ""]
    L += [f"Gerado em {rel['gerado_em']}.", "", f"**Alvo:** {rel['alvo']}.", "",
          "## Dados", "",
          f"- Arquivo: `{d['arquivo']}` (SHA-256 `{d['sha256'][:16]}…`)",
          f"- Taxa do desfecho nos dados usados: {rel.get('taxa_base', 0):.2%}",
          f"- Linhas lidas: {d['linhas_lidas']:,} | casos usados: {d['casos_usados']:,} (positivos {d['positivos']:,}; negativos {d['negativos']:,})",
          f"- Divisão {d['divisao']}: treino {d['treino']:,}, validação {d['validacao']:,}, teste {d['teste']:,}", "",
          "## Resultado no conjunto de teste (usado uma única vez)", "",
          "| Modelo | ROC-AUC | F1 | Sensibilidade | Especificidade | Acurácia |", "| --- | --- | --- | --- | --- | --- |",
          f"| Regressão logística (exportado) | {r['roc_auc']} | {r['f1']} | {r['sensibilidade']} | {r['especificidade']} | {r['acuracia']} |",
          f"| Árvores (referência) | {g['roc_auc']} | {g['f1']} | {g['sensibilidade']} | {g['especificidade']} | {g['acuracia']} |", "",
          f"IC 95% (bootstrap) da regressão logística: ROC-AUC {r['roc_auc_ic95']}; F1 {r['f1_ic95']}. Limiar escolhido na validação: {r['limiar']}.", "",
          "Matriz de confusão (teste): " + str(r["matriz_confusao"]), "",
          "## Razões de chances (quanto cada variável aumenta a chance do desfecho)", "",
          "| Variável | Razão de chances |", "| --- | --- |"]
    L += [f"| {x['variavel']} | {x['razao_de_chances']} |" for x in rel["razoes_de_chances"]]
    L += ["", "## Calibração (teste)", "", "| Faixa prevista | n | previsto | observado |", "| --- | --- | --- | --- |"]
    L += [f"| {c['faixa']} | {c['n']} | {c['previsto']} | {c['observado']} |" for c in rel["resultados_teste"]["calibracao_teste"]]
    return "\n".join(L) + "\n"


def grafico(base: Path, y, p_lr, p_gb, rel):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from sklearn.metrics import roc_curve
    except Exception:
        return
    fig, ax = plt.subplots(1, 2, figsize=(10, 4))
    for p, nome in ((p_lr, "Regressão logística"), (p_gb, "Árvores (referência)")):
        fpr, tpr, _ = roc_curve(y, p)
        ax[0].plot(fpr, tpr, label=nome)
    ax[0].plot([0, 1], [0, 1], "--", color="gray", label="Acaso")
    ax[0].set_xlabel("1 - especificidade"); ax[0].set_ylabel("Sensibilidade"); ax[0].set_title("Curva ROC (teste)"); ax[0].legend()
    cal = rel["calibracao_teste"]
    ax[1].plot([0, 1], [0, 1], "--", color="gray")
    ax[1].plot([c["previsto"] for c in cal], [c["observado"] for c in cal], "o-")
    ax[1].set_xlabel("Probabilidade prevista"); ax[1].set_ylabel("Proporção observada"); ax[1].set_title("Calibração (teste)")
    fig.tight_layout()
    fig.savefig(base.with_suffix(".png"), dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
