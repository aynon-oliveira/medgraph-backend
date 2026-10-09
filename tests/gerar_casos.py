"""Gera casos aleatórios com o resultado do Python (app/services/ia_risco.py) para o teste de paridade com o site.
Uso (na pasta do back-end, com o modelo real ou de teste):  python tests/gerar_casos.py app/ia/modelo_risco.json casos.json
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services import ia_risco  # noqa: E402

modelo = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
ia_risco.validar(modelo)
rotulos = [f["rotulo"] for f in modelo["features"] if f.get("rotulo") and f["tipo"] == "binaria"]
nomes = [f["nome"] for f in modelo["features"] if f["tipo"] == "binaria"]
sinonimos = list(ia_risco.SINONIMOS) + ["  FEBRE ", "Dor de Cabeça", "Dor atrás dos olhos", "Vômito", "algo desconhecido", ""]
pool = rotulos + nomes + sinonimos
rnd = random.Random(7)
casos = []
for _ in range(2000):
    sint = [rnd.choice(pool) for _ in range(rnd.randint(0, 8))]
    idade = rnd.choice([None, -3, 0, 1, 7, 18, 30, 45, 64, 90, 110, 111, 200, rnd.uniform(0, 100)])
    sexo = rnd.choice([None, "", "F", "M", "O", "f"])
    casos.append({"sintomas": sint, "idade": idade, "sexo": sexo,
                  "esperado": ia_risco.pontuar(modelo, sint, idade, sexo)})
Path(sys.argv[2]).write_text(json.dumps({"modelo": modelo, "casos": casos}, ensure_ascii=False), encoding="utf-8")
print(f"{len(casos)} casos gravados em {sys.argv[2]}")
