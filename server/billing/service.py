"""Credit business logic: balance, hold/debit/release, top-up."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from server.billing.models import CreditBalance, CreditLedger


async def get_balance(db: AsyncSession, tenant_id: str) -> dict[str, Any]:
    """Return the current credit balance and monthly spend for a tenant.

    If no balance row exists yet the tenant is treated as having zero credits.
    """
    result = await db.execute(
        select(CreditBalance).where(CreditBalance.tenant_id == tenant_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return {"balance": 0.0, "spend_this_month": 0.0}
    return {
        "balance": float(row.balance),
        "spend_this_month": float(row.spend_this_month),
    }


async def check_and_hold(
    db: AsyncSession,
    tenant_id: str,
    amount: float,
    operation: str,
    workflow_id: str | None = None,
    user_id: str | None = None,
) -> int:
    """Place a hold on credits before executing an operation.

    Atomically checks the balance, deducts the hold amount, and writes a
    ``hold`` entry to the ledger.  Returns the ledger entry ID that serves
    as the hold identifier.

    Raises ``ValueError`` if the tenant has insufficient credits.
    """
    result = await db.execute(
        select(CreditBalance).where(CreditBalance.tenant_id == tenant_id).with_for_update()
    )
    balance_row = result.scalar_one_or_none()

    current = float(balance_row.balance) if balance_row else 0.0
    if current < amount:
        raise ValueError(
            f"Insufficient credits: need {amount}, have {current}"
        )

    new_balance = current - amount

    # Update balance
    if balance_row:
        balance_row.balance = new_balance  # type: ignore[assignment]
        balance_row.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
    else:
        # Should not normally happen if signup bonus was applied
        balance_row = CreditBalance(
            tenant_id=tenant_id,
            balance=new_balance,
            spend_this_month=0.0,
            month_reset_at=datetime.now(timezone.utc),
        )
        db.add(balance_row)

    # Write ledger entry
    entry = CreditLedger(
        tenant_id=tenant_id,
        user_id=user_id,
        entry_type="hold",
        amount=amount,
        balance_after=new_balance,
        operation=operation,
        workflow_id=workflow_id,
        description=f"Hold for {operation}",
    )
    db.add(entry)
    await db.flush()  # populate entry.id
    await db.commit()
    return entry.id


async def confirm_debit(db: AsyncSession, hold_id: int) -> None:
    """Convert a hold into a confirmed debit.

    Reads the original hold entry and writes a corresponding ``debit``
    entry.  The balance is not changed because it was already deducted
    during the hold step.
    """
    result = await db.execute(
        select(CreditLedger).where(CreditLedger.id == hold_id)
    )
    hold_entry = result.scalar_one_or_none()
    if hold_entry is None:
        raise ValueError(f"Hold {hold_id} not found")
    if hold_entry.entry_type != "hold":
        raise ValueError(f"Entry {hold_id} is not a hold (is {hold_entry.entry_type})")

    # Update monthly spend
    await db.execute(
        update(CreditBalance)
        .where(CreditBalance.tenant_id == hold_entry.tenant_id)
        .values(
            spend_this_month=CreditBalance.spend_this_month + hold_entry.amount,
            updated_at=datetime.now(timezone.utc),
        )
    )

    # Write debit entry (balance unchanged since hold already deducted)
    debit = CreditLedger(
        tenant_id=hold_entry.tenant_id,
        user_id=hold_entry.user_id,
        entry_type="debit",
        amount=hold_entry.amount,
        balance_after=hold_entry.balance_after,
        operation=hold_entry.operation,
        workflow_id=hold_entry.workflow_id,
        reference_id=str(hold_id),
        description=f"Confirmed debit for {hold_entry.operation}",
    )
    db.add(debit)
    await db.commit()


async def release_hold(db: AsyncSession, hold_id: int) -> None:
    """Release a hold, restoring credits to the tenant's balance.

    Used when an operation fails and the held credits should be returned.
    """
    result = await db.execute(
        select(CreditLedger).where(CreditLedger.id == hold_id)
    )
    hold_entry = result.scalar_one_or_none()
    if hold_entry is None:
        raise ValueError(f"Hold {hold_id} not found")
    if hold_entry.entry_type != "hold":
        raise ValueError(f"Entry {hold_id} is not a hold (is {hold_entry.entry_type})")

    # Restore balance
    bal_result = await db.execute(
        select(CreditBalance)
        .where(CreditBalance.tenant_id == hold_entry.tenant_id)
        .with_for_update()
    )
    balance_row = bal_result.scalar_one()
    new_balance = float(balance_row.balance) + float(hold_entry.amount)
    balance_row.balance = new_balance  # type: ignore[assignment]
    balance_row.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]

    # Write release entry
    release = CreditLedger(
        tenant_id=hold_entry.tenant_id,
        user_id=hold_entry.user_id,
        entry_type="release",
        amount=hold_entry.amount,
        balance_after=new_balance,
        operation=hold_entry.operation,
        workflow_id=hold_entry.workflow_id,
        reference_id=str(hold_id),
        description=f"Released hold for {hold_entry.operation}",
    )
    db.add(release)
    await db.commit()


async def add_credits(
    db: AsyncSession,
    tenant_id: str,
    amount: float,
    source: str,
    reference_id: str | None = None,
) -> float:
    """Add credits to a tenant's balance (signup bonus, top-up, etc.).

    Returns the new balance after the addition.
    """
    result = await db.execute(
        select(CreditBalance)
        .where(CreditBalance.tenant_id == tenant_id)
        .with_for_update()
    )
    balance_row = result.scalar_one_or_none()

    if balance_row:
        new_balance = float(balance_row.balance) + amount
        balance_row.balance = new_balance  # type: ignore[assignment]
        balance_row.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
    else:
        new_balance = amount
        balance_row = CreditBalance(
            tenant_id=tenant_id,
            balance=new_balance,
            spend_this_month=0.0,
            month_reset_at=datetime.now(timezone.utc),
        )
        db.add(balance_row)

    entry = CreditLedger(
        tenant_id=tenant_id,
        entry_type="credit",
        amount=amount,
        balance_after=new_balance,
        operation=source,
        reference_id=reference_id,
        description=f"Credit: {source}",
    )
    db.add(entry)
    await db.commit()
    return new_balance


async def get_history(
    db: AsyncSession,
    tenant_id: str,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[CreditLedger], int]:
    """Return paginated credit ledger entries and total count for a tenant."""
    count_result = await db.execute(
        select(func.count()).select_from(CreditLedger).where(
            CreditLedger.tenant_id == tenant_id
        )
    )
    total = count_result.scalar_one()

    result = await db.execute(
        select(CreditLedger)
        .where(CreditLedger.tenant_id == tenant_id)
        .order_by(CreditLedger.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    entries = list(result.scalars().all())
    return entries, total
