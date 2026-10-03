# Importar todos os modelos aqui garante que o SQLAlchemy conheça as tabelas
# antes de criá-las com Base.metadata.create_all().
from app.models.atendimento import Atendimento  # noqa: F401
from app.models.enums import NivelRisco, Perfil, StatusSincronizacao, StatusValidacao  # noqa: F401
from app.models.paciente import Paciente  # noqa: F401
from app.models.resultado_inferencia import ResultadoInferencia  # noqa: F401
from app.models.usuario import Usuario  # noqa: F401
