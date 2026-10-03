"""Teste simples: confere que os modelos foram carregados e as regras básicas existem."""
from app.models import Atendimento, Paciente, ResultadoInferencia, Usuario


def test_tabelas_registradas():
    assert Usuario.__tablename__ == "usuarios"
    assert Paciente.__tablename__ == "pacientes"
    assert Atendimento.__tablename__ == "atendimentos"
    assert ResultadoInferencia.__tablename__ == "resultados_inferencia"


def test_rn05_localizacao_obrigatoria():
    # RN05: todo atendimento precisa de coordenadas
    assert Atendimento.__table__.c.localizacao.nullable is False
