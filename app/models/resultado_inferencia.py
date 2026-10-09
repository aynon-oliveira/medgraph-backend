import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ResultadoInferencia(Base):
    """Saída do motor de IA para um atendimento (apoio à decisão, RN01)."""

    __tablename__ = "resultados_inferencia"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    atendimento_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("atendimentos.id", ondelete="CASCADE"), unique=True
    )
    score_probabilidade: Mapped[float] = mapped_column(Float)
    sinais_alarme: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    recomendacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    modelo_versao: Mapped[str | None] = mapped_column(String(60), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    atendimento = relationship("Atendimento", back_populates="resultado")
