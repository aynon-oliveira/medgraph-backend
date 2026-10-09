"""Gera um CSV SINTETICO parecido com o do SINAN, SO para testar o script de treino.

Os numeros daqui sao inventados: o modelo treinado com este arquivo NAO tem valor e NAO pode ir para o artigo.
    python ml/gerar_dados_sinteticos.py ml/SINTETICO_dengue_teste.csv
"""
import sys
import numpy as np
import pandas as pd

n = 60000
rs = np.random.RandomState(1)
grave = rs.rand(n) < 0.04                    # 11/12 (sinais de alarme/grave)
descartado = (~grave) & (rs.rand(n) < 0.25)  # 5 (so existe aqui para testar --alvo dengue)
P = {  # (prob. nos graves, prob. nos demais)
    "FEBRE": (.95, .90), "MIALGIA": (.80, .72), "CEFALEIA": (.75, .70), "EXANTEMA": (.25, .15),
    "VOMITO": (.45, .25), "NAUSEA": (.50, .30), "DOR_COSTAS": (.40, .30), "CONJUNTVIT": (.07, .05),
    "ARTRITE": (.08, .06), "ARTRALGIA": (.30, .22), "PETEQUIA_N": (.15, .05), "LEUCOPENI": (.12, .05),
    "LACO": (.06, .02), "DOR_RETRO": (.38, .28),
    "DIABETES": (.12, .03), "HEMATOLOG": (.03, .005), "HEPATOPAT": (.04, .01), "RENAL": (.05, .01),
    "HIPERTENSA": (.30, .10), "ACIDO_PEPT": (.05, .02), "AUTO_IMUNE": (.04, .01),
}
d = {}
for c, (a, b) in P.items():
    v = np.where(grave, rs.rand(n) < a, rs.rand(n) < b)
    d[c] = np.where(v, "1", "2")
idade = np.clip(rs.gamma(4.0, 9.0, n) + np.where(grave, 8, 0), 0, 95).astype(int)
d["NU_IDADE_N"] = ["4%03d" % i for i in idade]
d["CS_SEXO"] = rs.choice(["M", "F", "I"], n, p=[.47, .52, .01])
d["CS_GESTANT"] = rs.choice(["1", "2", "5", "6", "9"], n, p=[.01, .01, .35, .6, .03])
fin = np.where(grave, rs.choice([11, 12], n, p=[.93, .07]), np.where(descartado, 5, 10))
d["CLASSI_FIN"] = fin
d["CRITERIO"] = rs.choice([1, 2], n, p=[.45, .55])
dias = rs.randint(0, 540, n)
d["DT_NOTIFIC"] = (pd.Timestamp("2023-01-01") + pd.to_timedelta(dias, unit="D")).strftime("%Y-%m-%d")
pd.DataFrame(d).to_csv(sys.argv[1] if len(sys.argv) > 1 else "ml/SINTETICO_dengue_teste.csv", index=False)
print("gerado (SINTETICO, so para testar o codigo)")
