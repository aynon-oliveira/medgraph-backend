"""Mede a taxa de sincronização do MEDGRAPH-AM (RNF02) contra a API de verdade.

RNF02: "Sincronização com taxa mínima de 95% de sucesso, sem corrupção, duplicidade ou perda,
em testes controlados de reconexão."

Como funciona (simulação controlada de rede, do lado do celular):
  1. Gera N atendimentos "pendentes" (UUID criado no celular), uma parte com sinal de alarme (ALTO).
  2. Roda "janelas de conectividade". Em cada janela o celular tenta enviar a fila em lotes, ALTO primeiro (RN03).
     Cada tentativa pode falhar de três jeitos, sorteados com uma semente fixa (resultado reproduzível):
       - janela sem rede (nada é enviado);
       - queda ANTES de chegar ao servidor (o lote não chega);
       - queda DEPOIS do servidor gravar (o servidor gravou, mas o celular não recebe a confirmação
         e reenvia o mesmo lote: é aqui que duplicidade apareceria se a API não fosse idempotente).
  3. Ao final, confere no servidor, registro a registro: chegou? chegou uma vez só? está íntegro?
  4. taxa = registros íntegros no servidor / registros gerados.   Meta: >= 95%.

Uso (dentro do container, com a API no ar):
    docker compose exec api python -m tests.medir_sincronizacao --criar-acs \
        --gestor-email seu@email.com --gestor-senha SUA_SENHA

Isto NÃO substitui o teste em aparelho físico com o modo avião (TI02/TS02): mede a API e a lógica
de reenvio sob falhas de rede simuladas.
"""
import argparse
import json
import os
import random
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

META_PERCENTUAL = 95.0
FUSO = timezone(timedelta(hours=-4))  # Manaus

BAIRROS = [
    ("Compensa", -3.1150, -60.0560), ("Educandos", -3.1330, -60.0100), ("São José", -3.0740, -59.9640),
    ("Cidade Nova", -3.0230, -59.9840), ("Tarumã", -3.0300, -60.0600), ("Centro", -3.1319, -60.0233),
]
SINTOMAS_COMUNS = ["febre alta", "dor de cabeça", "dor retro-orbitária", "manchas na pele", "dor no corpo"]
SINAIS_ALARME = ["dor abdominal intensa", "vômitos persistentes", "sangramento de mucosa"]


# ------------------------------------------------------------------ cliente HTTP (só biblioteca padrão)
class ClienteHttp:
    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        self.token = None

    def _pedir(self, metodo, caminho, corpo=None, formulario=None, tempo_limite=30):
        dados, cab = None, {"Accept": "application/json"}
        if formulario is not None:
            dados = urllib.parse.urlencode(formulario).encode()
            cab["Content-Type"] = "application/x-www-form-urlencoded"
        elif corpo is not None:
            dados = json.dumps(corpo).encode()
            cab["Content-Type"] = "application/json"
        if self.token:
            cab["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.base + caminho, data=dados, headers=cab, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=tempo_limite) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"null")
            except ValueError:
                return e.code, None

    def entrar(self, email, senha):
        status, corpo = self._pedir("POST", "/auth/login", formulario={"username": email, "password": senha})
        if status != 200:
            raise SystemExit(f"Login recusado ({status}). Confira e-mail e senha.")
        self.token = corpo["access_token"]

    def registrar(self, nome, email, senha, perfil):
        return self._pedir("POST", "/auth/registrar", corpo={"nome": nome, "email": email, "senha": senha, "perfil": perfil})

    def sincronizar(self, lote):
        return self._pedir("POST", "/api/v1/sincronizar", corpo={"atendimentos": lote})

    def obter_atendimento(self, id_):
        status, corpo = self._pedir("GET", f"/atendimentos/{id_}")
        return corpo if status == 200 else None

    def listar_atendimentos(self):
        """Lê TODOS os atendimentos do usuário logado, página por página."""
        todos, offset = [], 0
        while True:
            status, pagina = self._pedir("GET", f"/atendimentos?limit=200&offset={offset}")
            if status != 200:
                raise SystemExit(f"Falha ao listar atendimentos ({status}).")
            todos.extend(pagina)
            if len(pagina) < 200:
                return todos
            offset += 200


# ------------------------------------------------------------------ geração dos atendimentos
def gerar_atendimentos(n, marca, rng, proporcao_alto=0.2):
    agora = datetime.now(FUSO).replace(microsecond=0)
    itens = []
    for i in range(n):
        bairro, lat, lon = rng.choice(BAIRROS)
        alto = rng.random() < proporcao_alto
        sintomas = rng.sample(SINTOMAS_COMUNS, k=rng.randint(1, 3))
        sinais = [rng.choice(SINAIS_ALARME)] if alto else []
        instante = (agora - timedelta(minutes=rng.randint(1, 600))).isoformat()
        itens.append({
            "id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
            "paciente": {"id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
                         "nome": f"Medicao Paciente {i:04d}", "sexo": rng.choice(["M", "F"])},
            "data_hora": instante,
            "atualizado_em": instante,
            "relato_texto": f"[{marca}] atendimento {i:04d}",
            "sintomas": sintomas + sinais,
            "latitude": round(lat + rng.uniform(-0.004, 0.004), 6),
            "longitude": round(lon + rng.uniform(-0.004, 0.004), 6),
            "bairro": bairro,
            "municipio": "Manaus",
            "resultado": {"score_probabilidade": round(rng.uniform(0.6, 0.97) if alto else rng.uniform(0.1, 0.6), 2),
                          "sinais_alarme": sinais,
                          "recomendacao": "Encaminhar" if alto else "Monitorar"},
        })
    return itens


def _alto(item):
    return bool(item["resultado"]["sinais_alarme"])


# ------------------------------------------------------------------ simulação de rede
class RedeSimulada:
    """Sorteia, para cada tentativa, se a rede funciona. Semente fixa = resultado reproduzível."""

    def __init__(self, rng, p_sem_rede=0.30, p_queda_antes=0.20, p_queda_depois=0.15):
        self.rng, self.p_sem_rede = rng, p_sem_rede
        self.p_antes, self.p_depois = p_queda_antes, p_queda_depois

    def janela_com_rede(self):
        return self.rng.random() >= self.p_sem_rede

    def tentativa(self):
        r = self.rng.random()
        if r < self.p_antes:
            return "QUEDA_ANTES"
        if r < self.p_antes + self.p_depois:
            return "QUEDA_DEPOIS"
        return "OK"


def executar(cliente, itens, rede, tamanho_lote=20, max_rodadas=40, relogio=time.perf_counter):
    """Roda as janelas de conectividade até a fila esvaziar ou acabarem as rodadas."""
    fila = sorted(itens, key=lambda i: 0 if _alto(i) else 1)  # RN03: ALTO risco primeiro
    por_id = {i["id"]: i for i in itens}
    confirmados, rodada_confirmacao = set(), {}
    stats = {"tentativas": 0, "ok": 0, "queda_antes": 0, "queda_depois": 0, "janelas_sem_rede": 0,
             "rejeitados_pelo_servidor": 0, "latencias_ms": [], "rodadas_usadas": 0}

    for rodada in range(1, max_rodadas + 1):
        pendentes = [i for i in fila if i["id"] not in confirmados]
        if not pendentes:
            break
        stats["rodadas_usadas"] = rodada
        if not rede.janela_com_rede():
            stats["janelas_sem_rede"] += 1
            continue
        for ini in range(0, len(pendentes), tamanho_lote):
            lote = pendentes[ini:ini + tamanho_lote]
            stats["tentativas"] += 1
            sorteio = rede.tentativa()
            if sorteio == "QUEDA_ANTES":
                stats["queda_antes"] += 1
                break  # perdeu a rede: termina a janela
            t0 = relogio()
            status, resp = cliente.sincronizar(lote)
            stats["latencias_ms"].append((relogio() - t0) * 1000)
            if sorteio == "QUEDA_DEPOIS" or status != 200:
                stats["queda_depois"] += 1  # o servidor pode ter gravado; o celular não sabe e reenvia
                break
            stats["ok"] += 1
            stats["rejeitados_pelo_servidor"] += resp.get("rejeitados", 0)
            for id_ in resp.get("ids_sincronizados", []):
                if id_ in por_id and id_ not in confirmados:
                    confirmados.add(id_)
                    rodada_confirmacao[id_] = rodada
    return confirmados, rodada_confirmacao, stats


# ------------------------------------------------------------------ verificação no servidor
def verificar(enviados, no_servidor, marca, buscar_por_id=None):
    """Compara registro a registro. Devolve contagens de chegou/íntegro/duplicado/corrompido/perdido.

    Cuidados para não acusar a sincronização por um defeito de LEITURA:
      - linhas repetidas na listagem paginada (mesmo id) são contadas à parte (`repetidos_na_listagem`);
      - duplicidade de verdade = ids DIFERENTES com o mesmo conteúdo (mesmo relato);
      - o que não apareceu na listagem é procurado direto pelo id (`buscar_por_id`) antes de ser dado como perdido.
    """
    meus = [a for a in no_servidor if (a.get("relato_texto") or "").startswith(f"[{marca}]")]
    por_id = {}
    for a in meus:
        por_id.setdefault(a["id"], []).append(a)
    repetidos_na_listagem = sum(1 for linhas in por_id.values() if len(linhas) > 1)
    servidor_por_id = {i: linhas[0] for i, linhas in por_id.items()}

    por_texto = {}
    for a in servidor_por_id.values():
        por_texto.setdefault(a["relato_texto"], []).append(a["id"])
    duplicados = sum(1 for ids in por_texto.values() if len(ids) > 1)

    recuperados = 0
    integros = perdidos = corrompidos = 0
    detalhes_corrupcao = []
    for env in enviados:
        srv = servidor_por_id.get(env["id"])
        if srv is None and buscar_por_id is not None:
            srv = buscar_por_id(env["id"])
            if srv is not None:
                recuperados += 1
        if srv is None:
            perdidos += 1
            continue
        problemas = []
        if abs(srv["latitude"] - env["latitude"]) > 1e-5 or abs(srv["longitude"] - env["longitude"]) > 1e-5:
            problemas.append("coordenadas")
        if srv.get("relato_texto") != env["relato_texto"]:
            problemas.append("relato")
        if sorted(srv.get("sintomas") or []) != sorted(s.lower() for s in env["sintomas"]):
            problemas.append("sintomas")
        if srv["paciente"]["nome"] != env["paciente"]["nome"] or srv["paciente"]["id"] != env["paciente"]["id"]:
            problemas.append("paciente")
        if _alto(env) and srv.get("nivel_risco") != "ALTO":
            problemas.append("nivel_risco (RN02)")
        if problemas:
            corrompidos += 1
            detalhes_corrupcao.append({"id": env["id"], "campos": problemas})
        else:
            integros += 1
    return {"no_servidor": len(servidor_por_id) + recuperados, "integros": integros, "perdidos": perdidos,
            "corrompidos": corrompidos, "duplicados": duplicados,
            "repetidos_na_listagem": repetidos_na_listagem, "recuperados_por_consulta_direta": recuperados,
            "detalhes_corrupcao": detalhes_corrupcao[:10]}


def montar_resultado(itens, confirmados, rodada_conf, stats, verif, parametros):
    n = len(itens)
    taxa = 100.0 * verif["integros"] / n if n else 0.0
    rodadas_alto = [rodada_conf[i["id"]] for i in itens if _alto(i) and i["id"] in rodada_conf]
    rodadas_outros = [rodada_conf[i["id"]] for i in itens if not _alto(i) and i["id"] in rodada_conf]
    lat = sorted(stats["latencias_ms"])
    p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else None
    return {
        "criterio": "RNF02: taxa >= 95% de atendimentos integros no servidor, sem perda, duplicidade ou corrupcao",
        "parametros": parametros,
        "atendimentos_gerados": n,
        "atendimentos_alto_risco": sum(1 for i in itens if _alto(i)),
        "confirmados_pelo_celular": len(confirmados),
        "no_servidor": verif["no_servidor"],
        "integros": verif["integros"],
        "perdidos": verif["perdidos"],
        "duplicados": verif["duplicados"],
        "corrompidos": verif["corrompidos"],
        "anomalias_de_leitura": {
            "repetidos_na_listagem": verif["repetidos_na_listagem"],
            "recuperados_por_consulta_direta": verif["recuperados_por_consulta_direta"],
        },
        "taxa_sincronizacao_percentual": round(taxa, 2),
        "atinge_rnf02": bool(taxa >= META_PERCENTUAL and verif["duplicados"] == 0 and verif["corrompidos"] == 0),
        "rede": {k: stats[k] for k in ("tentativas", "ok", "queda_antes", "queda_depois", "janelas_sem_rede",
                                      "rejeitados_pelo_servidor", "rodadas_usadas")},
        "latencia_lote_ms": {"media": round(statistics.mean(lat), 1) if lat else None,
                             "p95": round(p95, 1) if p95 is not None else None},
        "prioridade_rn03": {
            "rodada_media_confirmacao_alto_risco": round(statistics.mean(rodadas_alto), 2) if rodadas_alto else None,
            "rodada_media_confirmacao_demais": round(statistics.mean(rodadas_outros), 2) if rodadas_outros else None,
        },
        "detalhes_corrupcao": verif["detalhes_corrupcao"],
        "observacao": "Simulacao controlada de falhas de rede contra a API real. Nao substitui o teste em aparelho fisico (TI02/TS02).",
    }


def imprimir(res):
    r = res["rede"]
    print("\n=== Medição da sincronização (RNF02) ===")
    print(f"Atendimentos gerados ........ {res['atendimentos_gerados']} ({res['atendimentos_alto_risco']} de alto risco)")
    print(f"Íntegros no servidor ........ {res['integros']}")
    print(f"Perdidos / duplicados / corrompidos: {res['perdidos']} / {res['duplicados']} / {res['corrompidos']}")
    anom = res["anomalias_de_leitura"]
    if anom["repetidos_na_listagem"] or anom["recuperados_por_consulta_direta"]:
        print(f"ATENÇÃO (defeito de LEITURA, não de sincronização): {anom['repetidos_na_listagem']} registro(s) repetido(s) "
              f"na listagem paginada e {anom['recuperados_por_consulta_direta']} só encontrado(s) por consulta direta.")
    print(f"Tentativas de envio ......... {r['tentativas']} (ok {r['ok']}; queda antes {r['queda_antes']}; "
          f"queda depois da gravação {r['queda_depois']}; janelas sem rede {r['janelas_sem_rede']})")
    print(f"Rodadas usadas .............. {r['rodadas_usadas']}")
    pr = res["prioridade_rn03"]
    print(f"RN03 (rodada média de confirmação): alto risco {pr['rodada_media_confirmacao_alto_risco']} | demais {pr['rodada_media_confirmacao_demais']}")
    print(f"TAXA DE SINCRONIZAÇÃO ....... {res['taxa_sincronizacao_percentual']}%  (meta: >= {META_PERCENTUAL}%)")
    print("RESULTADO ................... " + ("ATINGE o RNF02" if res["atinge_rnf02"] else "NÃO ATINGE o RNF02"))
    print("Obs.: simulação de rede contra a API real; não substitui o teste em aparelho físico.\n")


# ------------------------------------------------------------------ programa principal
def principal(argv=None):
    ap = argparse.ArgumentParser(description="Mede a taxa de sincronização (RNF02).")
    ap.add_argument("--url", default=os.environ.get("MEDGRAPH_URL", "http://localhost:8000"))
    ap.add_argument("--acs-email", default=os.environ.get("MEDIR_ACS_EMAIL"))
    ap.add_argument("--acs-senha", default=os.environ.get("MEDIR_ACS_SENHA"))
    ap.add_argument("--criar-acs", action="store_true", help="cria um ACS temporário (precisa do GESTOR)")
    ap.add_argument("--gestor-email", default=os.environ.get("MEDIR_GESTOR_EMAIL"))
    ap.add_argument("--gestor-senha", default=os.environ.get("MEDIR_GESTOR_SENHA"))
    ap.add_argument("--atendimentos", type=int, default=200)
    ap.add_argument("--lote", type=int, default=20)
    ap.add_argument("--rodadas", type=int, default=40)
    ap.add_argument("--semente", type=int, default=42)
    ap.add_argument("--p-sem-rede", type=float, default=0.30)
    ap.add_argument("--p-queda-antes", type=float, default=0.20)
    ap.add_argument("--p-queda-depois", type=float, default=0.15)
    ap.add_argument("--saida", default="resultado_sincronizacao.json")
    a = ap.parse_args(argv)

    marca = "MEDICAO-" + uuid.uuid4().hex[:8]
    rng = random.Random(a.semente)
    cliente = ClienteHttp(a.url)

    if a.criar_acs:
        if not (a.gestor_email and a.gestor_senha):
            raise SystemExit("Para --criar-acs informe --gestor-email e --gestor-senha.")
        cliente.entrar(a.gestor_email, a.gestor_senha)
        a.acs_email = f"medicao{uuid.uuid4().hex[:8]}@medicao.medgraph"
        a.acs_senha = uuid.uuid4().hex + "Aa1"
        status, corpo = cliente.registrar("ACS Medicao", a.acs_email, a.acs_senha, "ACS")
        if status != 201:
            raise SystemExit(f"Não consegui criar o ACS temporário ({status}): {corpo}")
        cliente.token = None
    if not (a.acs_email and a.acs_senha):
        raise SystemExit("Informe --acs-email e --acs-senha, ou use --criar-acs.")
    cliente.entrar(a.acs_email, a.acs_senha)

    itens = gerar_atendimentos(a.atendimentos, marca, rng)
    rede = RedeSimulada(random.Random(a.semente + 1), a.p_sem_rede, a.p_queda_antes, a.p_queda_depois)
    print(f"Marca desta medição: {marca}. Enviando {len(itens)} atendimentos com falhas de rede simuladas...")
    confirmados, rodada_conf, stats = executar(cliente, itens, rede, a.lote, a.rodadas)
    verif = verificar(itens, cliente.listar_atendimentos(), marca, cliente.obter_atendimento)
    parametros = {k: getattr(a, k) for k in ("atendimentos", "lote", "rodadas", "semente", "p_sem_rede",
                                              "p_queda_antes", "p_queda_depois")}
    parametros["marca"] = marca
    res = montar_resultado(itens, confirmados, rodada_conf, stats, verif, parametros)
    res["data_hora"] = datetime.now(FUSO).isoformat(timespec="seconds")
    imprimir(res)
    with open(a.saida, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f"Resultado completo salvo em: {a.saida}")
    return 0 if res["atinge_rnf02"] else 1


if __name__ == "__main__":
    sys.exit(principal())
