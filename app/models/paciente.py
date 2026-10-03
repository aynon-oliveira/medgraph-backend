import uuid
from datetime import date

from sqlalchemy import Date, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Paciente(Base):
    """Dados mínimos do paciente (princípio da minimização da LGPD)."""

    __tablename__ = "pacientes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nome: Mapped[str] = mapped_column(String(150))
    data_nascimento: Mapped[date | None] = mapped_column(Date, nullable=True)
    sexo: Mapped[str | None] = mapped_column(String(1), nullable=True)  # M / F / O

    atendimentos = relationship("Atendimento", back_populates="paciente")
