"""Passo 29 - privacidade do Gestor e do mapa (copia de seguranca *.bak-passo29).

Uso, na pasta medgraph-backend (depois de copiar os arquivos novos do passo):
    python scripts/aplicar_passo29.py

Pode rodar de novo sem problema: o que ja foi aplicado e pulado. Se algum trecho nao for encontrado,
o script PARA antes de gravar qualquer coisa nesse arquivo e avisa.
"""
import pathlib
import shutil
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent


def ler(caminho):
    bruto = caminho.read_bytes().decode("utf-8")
    return bruto.replace("\r\n", "\n"), "\r\n" in bruto


def gravar(caminho, texto, crlf):
    shutil.copy2(caminho, caminho.with_name(caminho.name + ".bak-passo29"))
    caminho.write_bytes((texto.replace("\n", "\r\n") if crlf else texto).encode("utf-8"))


def aplicar(rel, trocas, ja_aplicado, acrescentar=""):
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
    if acrescentar:
        texto = texto.rstrip("\n") + "\n" + acrescentar
    gravar(caminho, texto, crlf)
    print(f"  {rel}: aplicado.")


print("Passo 29 - aplicando...")

# 1) Gestor recebe o atendimento sem identificar o paciente
aplicar("app/routers/atendimentos.py", [
    ("from app.services import auditoria_service\n",
     "from app.services import auditoria_service, privacidade\n"),
    ('    """Lista atendimentos. O ACS vê só os seus; Médico e Gestor veem todos."""\n',
     '    """Lista atendimentos. O ACS vê só os seus; Médico e Gestor veem todos.\n\n'
     '    O Gestor recebe a versão sem identificação do paciente (LGPD: ele trabalha com dados agregados)."""\n'),
    ("    saida = [_para_saida(a) for a in db.scalars(consulta).all()]\n"
     "    auditoria_service.registrar_acesso(\n"
     "        db, usuario, auditoria_service.LISTAR_ATENDIMENTOS,",
     "    saida = [_para_saida(a) for a in db.scalars(consulta).all()]\n"
     "    if usuario.perfil == Perfil.GESTOR:\n"
     "        saida = [privacidade.anonimizar_para_gestor(s) for s in saida]\n"
     "    auditoria_service.registrar_acesso(\n"
     "        db, usuario, auditoria_service.LISTAR_ATENDIMENTOS,"),
    ("    saida = _para_saida(atendimento)\n"
     "    auditoria_service.registrar_acesso(\n"
     "        db, usuario, auditoria_service.LER_ATENDIMENTO,",
     "    saida = _para_saida(atendimento)\n"
     "    if usuario.perfil == Perfil.GESTOR:\n"
     "        saida = privacidade.anonimizar_para_gestor(saida)\n"
     "    auditoria_service.registrar_acesso(\n"
     "        db, usuario, auditoria_service.LER_ATENDIMENTO,"),
], "privacidade.anonimizar_para_gestor(saida)")

# 2) Mapa: localidade com poucos casos sai com localizacao aproximada
aplicar("app/routers/mapa.py", [
    ("from app.services import neo4j_service\n", "from app.services import neo4j_service, privacidade\n"),
    ('''    consulta = text(
        """
        SELECT id,
               nivel_risco::text AS nivel_risco,
               status_validacao::text AS status_validacao,
               data_hora,
               municipio,
               bairro,
               ST_X(localizacao::geometry) AS longitude,
               ST_Y(localizacao::geometry) AS latitude
        FROM atendimentos
        WHERE localizacao IS NOT NULL
          AND (CAST(:nivel AS text) IS NULL OR nivel_risco::text = CAST(:nivel AS text))
          AND (CAST(:desde AS timestamptz) IS NULL OR data_hora >= CAST(:desde AS timestamptz))
          AND (CAST(:ate AS timestamptz) IS NULL OR data_hora <= CAST(:ate AS timestamptz))
        ORDER BY data_hora DESC
        LIMIT :limite
        """
    )
''', '''    # casos_localidade conta TODOS os casos da localidade (antes dos filtros), para que filtrar por risco
    # ou por data nao faca uma localidade grande parecer pequena (nem o contrario).
    consulta = text(
        """
        WITH base AS (
            SELECT id,
                   nivel_risco::text AS nivel_risco,
                   status_validacao::text AS status_validacao,
                   data_hora,
                   municipio,
                   bairro,
                   ST_X(localizacao::geometry) AS longitude,
                   ST_Y(localizacao::geometry) AS latitude,
                   count(*) OVER (PARTITION BY municipio, bairro) AS casos_localidade
            FROM atendimentos
            WHERE localizacao IS NOT NULL
        )
        SELECT * FROM base
        WHERE (CAST(:nivel AS text) IS NULL OR nivel_risco = CAST(:nivel AS text))
          AND (CAST(:desde AS timestamptz) IS NULL OR data_hora >= CAST(:desde AS timestamptz))
          AND (CAST(:ate AS timestamptz) IS NULL OR data_hora <= CAST(:ate AS timestamptz))
        ORDER BY data_hora DESC
        LIMIT :limite
        """
    )
'''),
    ('''    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [round(l.longitude, CASAS_DECIMAIS), round(l.latitude, CASAS_DECIMAIS)],
            },
            "properties": {
                "atendimento_id": str(l.id),
                "nivel_risco": l.nivel_risco,
                "status_validacao": l.status_validacao,
                "data_hora": l.data_hora.isoformat() if l.data_hora else None,
                "municipio": l.municipio,
                "bairro": l.bairro,
            },
        }
        for l in linhas
    ]
''', '''    features = []
    for l in linhas:
        lon, lat, aproximado = privacidade.arredondar_coordenadas(l.longitude, l.latitude, l.casos_localidade)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "atendimento_id": str(l.id),
                    "nivel_risco": l.nivel_risco,
                    "status_validacao": l.status_validacao,
                    "data_hora": l.data_hora.isoformat() if l.data_hora else None,
                    "municipio": l.municipio,
                    "bairro": l.bairro,
                    "localizacao_aproximada": aproximado,  # poucos casos na localidade: ponto menos preciso
                },
            }
        )
'''),
    ('''        total = g["total"]
        features.append(
''', '''        total = g["total"]
        lon, lat, aproximado = privacidade.arredondar_coordenadas(g["soma_lon"] / total, g["soma_lat"] / total, total)
        features.append(
'''),
    ('''                    "coordinates": [
                        round(g["soma_lon"] / total, CASAS_DECIMAIS),
                        round(g["soma_lat"] / total, CASAS_DECIMAIS),
                    ],
''', '''                    "coordinates": [lon, lat],
'''),
    ('''                    "proporcao_alto_risco": round(g["alto"] / total, 3),
''', '''                    "proporcao_alto_risco": round(g["alto"] / total, 3),
                    "localizacao_aproximada": aproximado,
'''),
], "privacidade.arredondar_coordenadas")

# 3) Testes existentes do mapa passam a esperar a localizacao aproximada (2 casos < 3)
aplicar("tests/test_mapa.py", [
    ("LAT_ARRED, LON_ARRED = -3.123, -60.023\n",
     "LAT_ARRED, LON_ARRED = -3.123, -60.023\nLAT_APROX, LON_APROX = -3.12, -60.02  # localidade com menos de 3 casos (passo 29)\n"),
    ('    assert feature["geometry"]["coordinates"] == [LON_ARRED, LAT_ARRED]\n',
     '    assert feature["geometry"]["coordinates"] == [LON_APROX, LAT_APROX]  # Centro tem 2 casos (< 3)\n'
     '    assert feature["properties"]["localizacao_aproximada"] is True\n'),
    ('    assert _localidade(r, f"Centro, {MUN}")["geometry"]["coordinates"] == [LON_ARRED, LAT_ARRED]\n',
     '    assert _localidade(r, f"Centro, {MUN}")["geometry"]["coordinates"] == [LON_APROX, LAT_APROX]\n'),
], "LAT_APROX", acrescentar='''

# ------------------------------------------------------------------ passo 29: localizacao aproximada


def test_localidade_com_poucos_casos_e_marcada_como_aproximada(client, dados, gestor):
    r = client.get("/api/v1/mapa/atendimentos?limit=5000", headers=gestor)
    ponto = next(f for f in r.json()["features"] if f["properties"]["atendimento_id"] == dados["c"])
    assert ponto["properties"]["localizacao_aproximada"] is True
    d = client.get("/api/v1/mapa/densidade", headers=gestor)
    assert _localidade(d, f"Cidade Nova, {MUN}")["properties"]["localizacao_aproximada"] is True


def test_localidade_com_3_casos_mantem_a_precisao_normal(client, acs, gestor):
    bairro = f"Grande{SUF}"
    ids = []
    for _ in range(3):
        r = client.post("/atendimentos", headers=acs, json=_payload(bairro=bairro))
        assert r.status_code == 201
        _PACIENTES.append(r.json()["paciente"]["id"])
        ids.append(r.json()["id"])
    r = client.get("/api/v1/mapa/atendimentos?limit=5000", headers=gestor)
    ponto = next(f for f in r.json()["features"] if f["properties"]["atendimento_id"] == ids[0])
    assert ponto["geometry"]["coordinates"] == [LON_ARRED, LAT_ARRED]
    assert ponto["properties"]["localizacao_aproximada"] is False
    # filtrar por risco nao muda a regra: ela vale para a localidade inteira
    so_alto = client.get("/api/v1/mapa/atendimentos?nivel_risco=ALTO&limit=5000", headers=gestor)
    assert ids[0] not in _ids(so_alto)
''')

# 4) Teste novo: Gestor nao ve o paciente identificado
aplicar("tests/test_atendimentos.py", [], "test_gestor_ve_o_atendimento_sem_identificar_o_paciente", acrescentar='''

def test_gestor_ve_o_atendimento_sem_identificar_o_paciente(client, acs1, gestor):
    """Passo 29 (LGPD): o Gestor trabalha com dados agregados; o ACS continua vendo a ficha completa."""
    nome = f"Paciente Teste {uuid.uuid4().hex[:6]}"
    criado = client.post(
        "/atendimentos",
        headers=acs1,
        json=_payload(
            paciente={"nome": nome, "sexo": "F", "data_nascimento": "1990-05-17"},
            rua="Rua das Flores, 123",
            latitude=-3.1234567,
            longitude=-60.0234567,
        ),
    ).json()

    do_acs = client.get(f"/atendimentos/{criado['id']}", headers=acs1).json()
    assert do_acs["paciente"]["nome"] == nome and do_acs["rua"] == "Rua das Flores, 123"

    g = client.get(f"/atendimentos/{criado['id']}", headers=gestor).json()
    assert g["paciente"]["nome"] != nome and g["paciente"]["nome"].startswith("Paciente ")
    assert g["paciente"]["data_nascimento"] is None
    assert g["relato_texto"] is None and g["rua"] is None
    assert (g["longitude"], g["latitude"]) == (-60.02, -3.12)
    assert g["sintomas"] == do_acs["sintomas"] and g["nivel_risco"] == do_acs["nivel_risco"]

    lista = client.get("/atendimentos?limit=200", headers=gestor)
    assert lista.status_code == 200
    assert nome not in lista.text and "Rua das Flores" not in lista.text
''')

print("\nPronto. Proximo: docker compose up -d --build   e depois   docker compose exec api pytest -q")
