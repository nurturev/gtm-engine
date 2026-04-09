"""Pydantic v2 request/response schemas for the execution module."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, model_validator, Field


class ExecuteRequest(BaseModel):
    operation: str  # e.g. "enrich_person", "search_companies"
    provider: str | None = None  # auto-select if None
    params: dict[str, Any] = {}


class ExecuteResponse(BaseModel):
    execution_id: str
    status: str
    credits_charged: float
    result: dict[str, Any]
    balance_remaining: float | None = None


class CostEstimateRequest(BaseModel):
    operation: str
    params: dict[str, Any] = {}


class CostEstimateResponse(BaseModel):
    operation: str
    estimated_credits: float
    breakdown: str  # human-readable explanation
    is_free_with_byok: bool = True


class BulkCostEstimateRequest(BaseModel):
    operations: list[CostEstimateRequest] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="1–50 operations to estimate. Mixed operation types are allowed.",
    )


class BulkCostEstimateItem(BaseModel):
    index: int  # 0-based position in the request list
    operation: str
    estimated_credits: float
    breakdown: str


class BulkCostEstimateResponse(BaseModel):
    total_estimated_credits: float
    item_count: int
    is_free_with_byok: bool = True
    items: list[BulkCostEstimateItem]


class BatchExecuteRequest(BaseModel):
    operations: list[ExecuteRequest]

    @model_validator(mode="before")
    @classmethod
    def convert_legacy_format(cls, data: Any) -> Any:
        """Convert legacy ``{"operation": "...", "items": [...]}`` to the
        canonical ``{"operations": [{"operation": ..., "params": item}, ...]}``
        format so older CLI versions keep working."""
        if isinstance(data, dict) and "items" in data and "operations" not in data:
            operation = data.get("operation", "")
            items = data.get("items", [])
            data = {
                "operations": [
                    {"operation": operation, "params": item} for item in items
                ],
            }
        return data


class BatchExecuteResponse(BaseModel):
    batch_id: str
    total: int
    status: str  # "processing" | "completed"


class BatchStatusResponse(BaseModel):
    batch_id: str
    total: int
    completed: int
    failed: int
    status: str
    results: list[dict[str, Any]]
