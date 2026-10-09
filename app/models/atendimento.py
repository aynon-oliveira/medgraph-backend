import uuid
from datetime import datetime, timezone

from geoalchemy2 import Geography
from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import NivelRisco, StatusSincronizacao, StatusValidacao


def _agora():
    return datetime.now(timezone.utc)


class Atendimento(Base):
    """Visita/triagem feita pelo ACS em campo (RF02).

    O `id` é um UUID gerado NO CELULAR. Assim, se o app reenviar o mesmo
    atendimento na sincronização, o servidor reconhece e não duplica (RF07).
    """

    __tablename__ = "atendimentos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    agente_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id"), index=True)
    paciente_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pacientes.id"), index=True)
    medico_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)

    data_hora: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    relato_texto: Mapped[str | None] = mapped_column(Text, nullable=True)       # transcrição do áudio
    # Sintomas extraídos do relato pelo app (viram nós NoSintoma no grafo, RF09)
    sintomas: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")
    # Há quantos dias começaram os sintomas (0 a 30; opcional). Entra no modelo de risco.
    dias_sintomas: Mapped[int | None] = mapped_column(Integer, nullable=True)
    relato_voz_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    transcricao_audio: Mapped[str | None] = mapped_column(Text, nullable=True)  # rascunho do Whisper
    imagem_exantema_path: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # RN05: geolocalização obrigatória (nullable=False garante isso no banco)
    localizacao = mapped_column(Geography(geometry_type="POINT", srid=4326), nullable=False)
    rua: Mapped[str | None] = mapped_column(String(150), nullable=True)
    bairro: Mapped[str | None] = mapped_column(String(100), nullable=True)
    municipio: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Resultado da triagem (calculado pela IA no celular e enviado na sincronização)
    probabilidade_dengue: Mapped[float | None] = mapped_column(Float, nullable=True)
    nivel_risco: Mapped[NivelRisco | None] = mapped_column(Enum(NivelRisco, name="nivel_risco"), nullable=True)

    status_sincronizacao: Mapped[StatusSincronizacao] = mapped_column(
        Enum(StatusSincronizacao, name="status_sincronizacao"),
        default=StatusSincronizacao.SINCRONIZADO,
    )
    status_validacao: Mapped[StatusValidacao] = mapped_column(
        Enum(StatusValidacao, name="status_validacao"), default=StatusValidacao.PENDENTE
    )
    parecer_medico: Mapped[str | None] = mapped_column(Text, nullable=True)
    encaminhamento: Mapped[str | None] = mapped_column(Text, nullable=True)  # RF08: encaminhamento definitivo

    # RN06: em conflito entre dados do mesmo nível, vale o timestamp mais recente
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_agora, onupdate=_agora)

    agente = relationship("Usuario", back_populates="atendimentos_realizados", foreign_keys=[agente_id])
    medico = relationship("Usuario", back_populates="atendimentos_avaliados", foreign_keys=[medico_id])
    paciente = relationship("Paciente", back_populates="atendimentos")
    resultado = relationship("ResultadoInferencia", back_populates="atendimento", uselist=False)
