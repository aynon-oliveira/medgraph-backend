"""Testes do script de medição da sincronização (passo 14). Não precisam de banco nem da API no ar:
usam um servidor de mentira que imita a regra do /api/v1/sincronizar.

    docker compose exec api pytest tests/test_medicao.py -v
"""
import random

from tests import medir_sincronizacao as m


class ServidorFalso:
    """Imita a API: idempotente pelo id do celular (RF07) e ALTO risco quando há sinal de alarme (RN02)."""

    def __init__(self, idempotente=True, estraga_coordenadas=False, perde_um_a_cada=0):
        self.registros, self.idempotente = {}, idempotente
        self.estraga, self.perde_um_a_cada = estraga_coordenadas, perde_um_a_cada
        self.chamadas, self.ordem_de_chegada = 0, []

    def sincronizar(self, lote):
        self.chamadas += 1
        ids = []
        for item in lote:
            self.ordem_de_chegada.append(item["id"])
            if self.perde_um_a_cada and len(self.registros) % self.perde_um_a_cada == self.perde_um_a_cada - 1:
                continue  # servidor "esquece" o registro, mas diz que gravou
            chave = item["id"] if self.idempotente else f"{item['id']}-{self.chamadas}"
            if chave not in self.registros:
                self.registros[chave] = self._gravar(item, chave)
            ids.append(item["id"])
        return 200, {"ids_sincronizados": ids, "rejeitados": 0}

    def _gravar(self, item, chave):
        alto = bool(item["resultado"]["sinais_alarme"])
        return {
            "id": chave, "relato_texto": item["relato_texto"],
            "sintomas": [s.lower() for s in item["sintomas"]],
            "latitude": item["latitude"] + (1.0 if self.estraga else 0), "longitude": item["longitude"],
            "paciente": {"id": item["paciente"]["id"], "nome": item["paciente"]["nome"]},
            "nivel_risco": "ALTO" if alto else "BAIXO",
        }

    def listar_atendimentos(self):
        return list(self.registros.values())


class ClienteFalso:
    def __init__(self, servidor):
        self.s = servidor

    def sincronizar(self, lote):
        return self.s.sincronizar(lote)

    def listar_atendimentos(self):
        return self.s.listar_atendimentos()


def _rodar(servidor, n=120, semente=7, **rede_kw):
    marca = "MEDICAO-teste"
    itens = m.gerar_atendimentos(n, marca, random.Random(semente))
    rede = m.RedeSimulada(random.Random(semente + 1), **rede_kw)
    cliente = ClienteFalso(servidor)
    confirmados, rodadas, stats = m.executar(cliente, itens, rede, tamanho_lote=10, max_rodadas=60, relogio=lambda: 0.0)
    verif = m.verificar(itens, cliente.listar_atendimentos(), marca)
    return itens, confirmados, rodadas, stats, verif


def test_geracao_e_reprodutivel_e_tem_alto_risco():
    a = m.gerar_atendimentos(50, "X", random.Random(1))
    b = m.gerar_atendimentos(50, "X", random.Random(1))
    assert a == b
    assert any(m._alto(i) for i in a) and not all(m._alto(i) for i in a)
    assert len({i["id"] for i in a}) == 50


def test_com_falhas_de_rede_e_api_idempotente_nada_se_perde_nem_duplica():
    servidor = ServidorFalso()
    itens, confirmados, _, stats, verif = _rodar(servidor, p_sem_rede=0.3, p_queda_antes=0.2, p_queda_depois=0.2)
    assert stats["queda_depois"] > 0 and stats["queda_antes"] > 0  # as falhas realmente aconteceram
    assert len(confirmados) == len(itens)
    assert verif["integros"] == len(itens)
    assert verif["perdidos"] == verif["duplicados"] == verif["corrompidos"] == 0


def test_resultado_atinge_rnf02_quando_tudo_chega():
    itens, confirmados, rodadas, stats, verif = _rodar(ServidorFalso())
    res = m.montar_resultado(itens, confirmados, rodadas, stats, verif, {})
    assert res["taxa_sincronizacao_percentual"] == 100.0
    assert res["atinge_rnf02"] is True


def test_detecta_duplicidade_se_a_api_nao_fosse_idempotente():
    servidor = ServidorFalso(idempotente=False)
    _, _, _, _, verif = _rodar(servidor, p_sem_rede=0.0, p_queda_antes=0.0, p_queda_depois=0.5)
    assert verif["duplicados"] > 0


def test_detecta_corrupcao():
    _, _, _, _, verif = _rodar(ServidorFalso(estraga_coordenadas=True), p_sem_rede=0.0, p_queda_antes=0.0, p_queda_depois=0.0)
    assert verif["corrompidos"] > 0
    assert "coordenadas" in verif["detalhes_corrupcao"][0]["campos"]


def test_detecta_perda_e_reprova_o_rnf02():
    servidor = ServidorFalso(perde_um_a_cada=4)  # servidor confirma mas não grava 1 em cada 4
    itens, confirmados, rodadas, stats, verif = _rodar(servidor, p_sem_rede=0.0, p_queda_antes=0.0, p_queda_depois=0.0)
    res = m.montar_resultado(itens, confirmados, rodadas, stats, verif, {})
    assert verif["perdidos"] > 0
    assert res["taxa_sincronizacao_percentual"] < 95
    assert res["atinge_rnf02"] is False


def test_alto_risco_e_enviado_primeiro_rn03():
    servidor = ServidorFalso()
    itens, *_ = _rodar(servidor, p_sem_rede=0.0, p_queda_antes=0.0, p_queda_depois=0.0)
    alto = {i["id"] for i in itens if m._alto(i)}
    primeiros = servidor.ordem_de_chegada[: len(alto)]
    assert set(primeiros) == alto


def test_sem_rede_nenhuma_rodada_nada_chega_e_a_taxa_e_zero():
    servidor = ServidorFalso()
    itens, confirmados, rodadas, stats, verif = _rodar(servidor, p_sem_rede=1.0)
    res = m.montar_resultado(itens, confirmados, rodadas, stats, verif, {})
    assert res["taxa_sincronizacao_percentual"] == 0.0
    assert res["atinge_rnf02"] is False


def test_linha_repetida_na_listagem_nao_vira_duplicidade_nem_reprova():
    """Defeito de paginação (linha repetida) é de LEITURA: não pode ser confundido com duplicidade na sincronização."""
    servidor = ServidorFalso()
    itens, confirmados, rodadas, stats, _ = _rodar(servidor, p_sem_rede=0.0, p_queda_antes=0.0, p_queda_depois=0.0)
    listagem = servidor.listar_atendimentos() + [servidor.listar_atendimentos()[0]]  # a mesma linha em duas páginas
    verif = m.verificar(itens, listagem, "MEDICAO-teste")
    assert verif["duplicados"] == 0
    assert verif["repetidos_na_listagem"] == 1
    assert verif["integros"] == len(itens)


def test_registro_que_sumiu_da_listagem_e_achado_por_consulta_direta():
    servidor = ServidorFalso()
    itens, *_ = _rodar(servidor, p_sem_rede=0.0, p_queda_antes=0.0, p_queda_depois=0.0)
    listagem = servidor.listar_atendimentos()
    faltou = listagem.pop(3)  # a listagem "pulou" um registro
    sem_busca = m.verificar(itens, listagem, "MEDICAO-teste")
    assert sem_busca["perdidos"] == 1
    com_busca = m.verificar(itens, listagem, "MEDICAO-teste", buscar_por_id=lambda i: servidor.registros.get(i))
    assert com_busca["perdidos"] == 0 and com_busca["recuperados_por_consulta_direta"] == 1
    assert com_busca["integros"] == len(itens) and faltou["id"] in servidor.registros


def test_registro_realmente_ausente_continua_sendo_perda_mesmo_com_consulta_direta():
    servidor = ServidorFalso(perde_um_a_cada=4)
    itens, *_ = _rodar(servidor, p_sem_rede=0.0, p_queda_antes=0.0, p_queda_depois=0.0)
    verif = m.verificar(itens, servidor.listar_atendimentos(), "MEDICAO-teste", buscar_por_id=lambda i: servidor.registros.get(i))
    assert verif["perdidos"] > 0
