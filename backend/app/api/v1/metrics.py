from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class DeploymentFrequencyPoint(BaseModel):
    date: str
    count: int


class LeadTimePoint(BaseModel):
    date: str
    hours: float


class ChangeFailureRatePoint(BaseModel):
    date: str
    rate: float  # 0.0 – 1.0


class MttrPoint(BaseModel):
    date: str
    hours: float


class DoraMetricsResponse(BaseModel):
    deployment_frequency: list[DeploymentFrequencyPoint]
    lead_time: list[LeadTimePoint]
    change_failure_rate: list[ChangeFailureRatePoint]
    mttr: list[MttrPoint]
    deployment_frequency_band: Literal["elite", "high", "medium", "low"] = "low"
    lead_time_band: Literal["elite", "high", "medium", "low"] = "low"
    change_failure_rate_band: Literal["elite", "high", "medium", "low"] = "low"
    mttr_band: Literal["elite", "high", "medium", "low"] = "low"


@router.get("/dora", response_model=DoraMetricsResponse, summary="Get DORA metrics")
async def get_dora_metrics() -> DoraMetricsResponse:
    """
    DORA metrics endpoint — returns the four key DevOps Research and Assessment metrics.

    Currently returns empty series. Wire to the approvals/deployments store to populate real data:
    - deployment_frequency: count of successful deploys per day
    - lead_time: hours from first commit to production deploy
    - change_failure_rate: ratio of failed deploys
    - mttr: hours from failure detection to recovery
    """
    return DoraMetricsResponse(
        deployment_frequency=[],
        lead_time=[],
        change_failure_rate=[],
        mttr=[],
    )
