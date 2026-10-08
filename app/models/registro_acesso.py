import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _agora():
    return datetime.now(timezone.utc)


class RegistroAcesso(Base):
    """Quem consultou dados de saúde, quando e de onde (LGPD, art. 37: registro das operações com dados).

    De propósito NÃO há chave estrangeira para `usuarios`: o histórico não pode sumir nem travar
    se um usuário for removido. O conteúdo consultado nunca é gravado, só a referência (tipo e id).
    """

    __tablename__ = "registros_acesso"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_agora, index=True)
    usuario_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    perfil: Mapped[str] = mapped_column(String(20))
    acao: Mapped[str] = mapped_column(String(40), index=True)
    recurso: Mapped[str] = mapped_column(String(40))
    recurso_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    detalhe: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
