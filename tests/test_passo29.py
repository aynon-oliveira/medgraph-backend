"""Passo 29 - testes das regras de privacidade (nao precisam de banco)."""
import uuid
from datetime import date, datetime, timezone

from app.models.enums import StatusSincronizacao, StatusValidacao
from app.schemas.atendimento import AtendimentoOut, PacienteOut
from app.services import privacidade


def _atendimento():
    pid = uuid.uuid4()
    return AtendimentoOut(
        id=uuid.uuid4(),
        agente_id=uuid.uuid4(),
        paciente=PacienteOut(id=pid, nome="Maria da Silva", data_nascimento=date(1990, 5, 17), sexo="F"),
        data_hora=datetime(2026, 10, 3, tzinfo=timezone.utc),
        relato_texto="Maria mora na Rua das Flores, 123",
        sintomas=["febre"],
        relato_voz_path="audio/abc.webm",
        imagem_exantema_path="foto/abc.jpg",
        latitude=-3.1234567,
        longitude=-60.0234567,
        rua="Rua das Flores, 123",
        bairro="Centro",
        municipio="Manaus",
        parecer_medico="Paciente Maria, telefone 9999",
        status_sincronizacao=StatusSincronizacao.SINCRONIZADO,
        status_validacao=StatusValidacao.PENDENTE,
        atualizado_em=datetime(2026, 10, 3, tzinfo=timezone.utc),
    )


def test_poucos_casos_gera_localizacao_aproximada():
    assert privacidade.arredondar_coordenadas(-60.0234567, -3.1234567, 1) == (-60.02, -3.12, True)
    assert privacidade.arredondar_coordenadas(-60.0234567, -3.1234567, 2) == (-60.02, -3.12, True)


def test_a_partir_do_minimo_mantem_tres_casas():
    assert privacidade.arredondar_coordenadas(-60.0234567, -3.1234567, 3) == (-60.023, -3.123, False)
    assert privacidade.arredondar_coordenadas(-60.0234567, -3.1234567, 40) == (-60.023, -3.123, False)


def test_nome_anonimo_e_estavel_e_nao_tem_o_nome():
    pid = uuid.uuid4()
    assert privacidade.nome_anonimo(pid) == privacidade.nome_anonimo(pid)
    assert privacidade.nome_anonimo(pid).startswith("Paciente ")
    assert privacidade.nome_anonimo(uuid.uuid4()) != privacidade.nome_anonimo(pid)


def test_gestor_recebe_a_versao_sem_identificacao():
    original = _atendimento()
    g = privacidade.anonimizar_para_gestor(original)
    assert g.paciente.nome != "Maria da Silva" and g.paciente.nome.startswith("Paciente ")
    assert g.paciente.data_nascimento is None
    assert g.paciente.sexo == "F"                       # dado agregavel, mantido
    assert g.relato_texto is None and g.rua is None and g.parecer_medico is None
    assert g.relato_voz_path is None and g.imagem_exantema_path is None
    assert (g.longitude, g.latitude) == (-60.02, -3.12)
    assert g.bairro == "Centro" and g.municipio == "Manaus" and g.sintomas == ["febre"]
    texto = g.model_dump_json()
    assert "Maria" not in texto and "Flores" not in texto and "9999" not in texto


def test_o_original_nao_e_alterado():
    original = _atendimento()
    privacidade.anonimizar_para_gestor(original)
    assert original.paciente.nome == "Maria da Silva"
    assert original.rua == "Rua das Flores, 123"
