"""Pydantic v2 request/response schemas for the execution module."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExecuteRequest(BaseModel):
    operation: str  # e.g. "enrich_person", "search_companies"
    provider: str | None = None  # auto-select if None
    params: dict[str, Any] = {}


class ExecuteResponse(BaseModel):
    execution_id: str
    status: str
    credits_charged: float
    result: dict[str, Any]


class CostEstimateRequest(BaseModel):
    operation: str
    provider: str | None = None
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


class BatchExecuteResponse(BaseModel):
    batch_id: str
    total: int
    status: str  # "completed" today; "processing" reserved for future async queue
    completed: int
    failed: int
    results: list[dict[str, Any]]


class BatchStatusResponse(BaseModel):
    batch_id: str
    total: int
    completed: int
    failed: int
    status: str
    results: list[dict[str, Any]]
