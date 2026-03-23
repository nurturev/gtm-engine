"""Credit ledger, balance, and payment models."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from server.core.database import Base


class CreditLedger(Base):
    """Immutable ledger of all credit movements for a tenant."""

    __tablename__ = "credit_ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(Text)
    entry_type: Mapped[str] = mapped_column(Text, nullable=False)  # credit|debit|hold|release
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    balance_after: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    operation: Mapped[str | None] = mapped_column(Text)
    reference_id: Mapped[str | None] = mapped_column(Text)
    workflow_id: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CreditBalance(Base):
    """Materialised credit balance for fast lookups."""

    __tablename__ = "credit_balances"

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    balance: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, server_default="0")
    spend_this_month: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False, server_default="0"
    )
    month_reset_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Payment(Base):
    """Stripe payment record linked to credit top-ups."""

    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # Stripe session ID
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    amount_usd: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    credits: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    package: Mapped[str | None] = mapped_column(Text)
    stripe_status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
