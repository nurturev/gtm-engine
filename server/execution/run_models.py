"""Run step model — tracks every MCP tool invocation for workflow logs."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from server.core.database import Base


class RunStep(Base):
    """A single step (MCP tool invocation) within a workflow.

    Workflows are computed aggregates: all RunSteps sharing the same
    workflow_id belong to the same Claude Code session.
    """

    __tablename__ = "run_steps"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(Text)
    workflow_id: Mapped[str] = mapped_column(Text, nullable=False)
    workflow_label: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    operation: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    params_summary: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    result_summary: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)
    credits_charged: Mapped[float] = mapped_column(
        Numeric(10, 2), server_default="0"
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
