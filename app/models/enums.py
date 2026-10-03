import enum


class Perfil(str, enum.Enum):
    ACS = "ACS"            # Agente Comunitário de Saúde
    MEDICO = "MEDICO"      # Médico / Especialista
    GESTOR = "GESTOR"      # Gestor de Vigilância Epidemiológica


class StatusSincronizacao(str, enum.Enum):
    PENDENTE_SYNC = "PENDENTE_SYNC"
    SINCRONIZADO = "SINCRONIZADO"


class NivelRisco(str, enum.Enum):
    BAIXO = "BAIXO"
    MODERADO = "MODERADO"
    ALTO = "ALTO"          # RN02: sinais de alarme => encaminhamento prioritário


class StatusValidacao(str, enum.Enum):
    PENDENTE = "PENDENTE"
    VALIDADO = "VALIDADO"
    REJEITADO = "REJEITADO"
