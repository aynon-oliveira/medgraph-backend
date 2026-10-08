import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import Perfil


class Usuario(Base):
    """Usuário do sistema. O campo `perfil` define o que ele pode fazer (RF01)."""

    __tablename__ = "usuarios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nome: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(255))  # nunca guardar a senha em texto puro
    perfil: Mapped[Perfil] = mapped_column(Enum(Perfil, name="perfil_usuario"))

    # Campos específicos de cada perfil (opcionais)
    cpf: Mapped[str | None] = mapped_column(String(14), unique=True, nullable=True)
    microarea: Mapped[str | None] = mapped_column(String(50), nullable=True)      # ACS
    crm: Mapped[str | None] = mapped_column(String(30), nullable=True)            # Médico
    departamento: Mapped[str | None] = mapped_column(String(100), nullable=True)  # Gestor

    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    # Passo 15: quando a senha foi trocada/redefinida. Tokens emitidos ANTES disso deixam de valer.
    senha_alterada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    atendimentos_realizados = relationship(
        "Atendimento", back_populates="agente", foreign_keys="Atendimento.agente_id"
    )
    atendimentos_avaliados = relationship(
        "Atendimento", back_populates="medico", foreign_keys="Atendimento.medico_id"
    )
