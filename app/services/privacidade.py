"""Passo 29 - privacidade (LGPD) para o Gestor e para o mapa.

Duas regras, em um lugar so:

1) O Gestor trabalha com dados agregados: quando ele consulta um atendimento, recebe a versao SEM
   identificacao (nome trocado por um codigo, sem data de nascimento, relato, rua, arquivos e parecer;
   coordenadas com 2 casas decimais, cerca de 1 km). O Medico e o ACS continuam vendo a ficha completa.

2) Localidade com poucos casos (menos de K_MINIMO) pode apontar a casa de uma pessoa. Nesse caso a
   coordenada sai com 2 casas decimais (cerca de 1 km) em vez de 3 (cerca de 110 m), e o ponto vem
   marcado como "localizacao_aproximada".
"""

K_MINIMO = 3          # menos casos que isto na localidade = localizacao aproximada
CASAS_NORMAL = 3      # cerca de 110 m
CASAS_APROXIMADO = 2  # cerca de 1,1 km


def arredondar_coordenadas(longitude: float, latitude: float, casos_na_localidade: int):
    """Devolve (longitude, latitude, aproximado)."""
    aproximado = casos_na_localidade < K_MINIMO
    casas = CASAS_APROXIMADO if aproximado else CASAS_NORMAL
    return round(longitude, casas), round(latitude, casas), aproximado


def nome_anonimo(paciente_id) -> str:
    """Codigo curto e estavel (o mesmo paciente sempre vira o mesmo codigo), sem relacao com o nome."""
    return "Paciente " + str(paciente_id).replace("-", "")[:6].upper()


def anonimizar_para_gestor(atendimento):
    """Recebe um AtendimentoOut e devolve uma copia sem os dados que identificam o paciente."""
    paciente = atendimento.paciente.model_copy(
        update={"nome": nome_anonimo(atendimento.paciente.id), "data_nascimento": None}
    )
    return atendimento.model_copy(
        update={
            "paciente": paciente,
            "relato_texto": None,
            "relato_voz_path": None,
            "imagem_exantema_path": None,
            "rua": None,
            "parecer_medico": None,
            "latitude": round(atendimento.latitude, CASAS_APROXIMADO),
            "longitude": round(atendimento.longitude, CASAS_APROXIMADO),
        }
    )
