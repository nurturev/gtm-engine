"""User connection model — tracks per-user Composio OAuth connections."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from server.core.database import Base


class UserConnection(Base):
    """Maps a user to their Composio connected account for an app.

    Composio only knows entity_id (opaque string). This table tracks which
    nrev-lite user owns each connection so we can attribute connections
    and implement per-user preference when executing actions.
    """

    __tablename__ = "user_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_email: Mapped[str] = mapped_column(Text, nullable=False)
    app_id: Mapped[str] = mapped_column(Text, nullable=False)
    composio_entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    composio_account_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
