from __future__ import annotations

import logging
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_settings

router = APIRouter()
logger = logging.getLogger(__name__)


class SastIssue(BaseModel):
    key: str
    severity: Literal["BLOCKER", "CRITICAL", "MAJOR", "MINOR", "INFO"]
    message: str
    component: str
    line: int | None = None
    rule: str


class SastResponse(BaseModel):
    issues: list[SastIssue]
    quality_gate: Literal["OK", "WARN", "ERROR", "NONE"]
    total_issues: int
    sonar_url: str | None = None


class ZapAlert(BaseModel):
    alert: str
    risk: Literal["High", "Medium", "Low", "Informational"]
    description: str
    solution: str
    url: str
    cweid: str | None = None


class DastResponse(BaseModel):
    alerts: list[ZapAlert]
    total_alerts: int
    high_risk: int
    medium_risk: int
    low_risk: int
    zap_url: str | None = None


@router.get("/sast", response_model=SastResponse, summary="Get SAST results from SonarQube")
async def sast_results() -> SastResponse:
    """
    Fetch SAST results from SonarQube.

    Requires SONAR_HOST_URL, SONAR_TOKEN environment variables.
    Returns empty results when not configured.
    """
    settings = get_settings()

    if not settings.sonar_host_url or not settings.sonar_token:
        logger.info("SonarQube not configured — returning empty SAST results")
        return SastResponse(issues=[], quality_gate="NONE", total_issues=0)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(
                f"{settings.sonar_host_url}/api/issues/search",
                params={"resolved": "false", "ps": 100},
                headers={"Authorization": f"Bearer {settings.sonar_token}"},
            )
        if res.status_code != 200:
            logger.warning("SonarQube API returned %d", res.status_code)
            return SastResponse(issues=[], quality_gate="NONE", total_issues=0)

        data = res.json()
        issues = [
            SastIssue(
                key=i.get("key", ""),
                severity=i.get("severity", "INFO"),
                message=i.get("message", ""),
                component=i.get("component", ""),
                line=i.get("line"),
                rule=i.get("rule", ""),
            )
            for i in data.get("issues", [])
        ]
        return SastResponse(
            issues=issues,
            quality_gate="OK",
            total_issues=data.get("total", len(issues)),
            sonar_url=settings.sonar_host_url,
        )
    except httpx.HTTPError as exc:
        logger.exception("SonarQube request failed")
        raise HTTPException(status_code=502, detail=f"SonarQube unreachable: {exc}") from exc


@router.get("/dast", response_model=DastResponse, summary="Get DAST results from OWASP ZAP")
async def dast_results() -> DastResponse:
    """
    Fetch DAST results from OWASP ZAP.

    Requires ZAP_BASE_URL and ZAP_API_KEY environment variables.
    Returns empty results when not configured.
    """
    settings = get_settings()

    if not settings.zap_base_url or not settings.zap_api_key:
        logger.info("OWASP ZAP not configured — returning empty DAST results")
        return DastResponse(alerts=[], total_alerts=0, high_risk=0, medium_risk=0, low_risk=0)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(
                f"{settings.zap_base_url}/JSON/alert/view/alerts/",
                params={"apikey": settings.zap_api_key},
            )
        if res.status_code != 200:
            logger.warning("ZAP API returned %d", res.status_code)
            return DastResponse(alerts=[], total_alerts=0, high_risk=0, medium_risk=0, low_risk=0)

        raw_alerts = res.json().get("alerts", [])
        alerts = [
            ZapAlert(
                alert=a.get("alert", ""),
                risk=a.get("risk", "Informational"),
                description=a.get("description", ""),
                solution=a.get("solution", ""),
                url=a.get("url", ""),
                cweid=a.get("cweid") or None,
            )
            for a in raw_alerts
        ]
        return DastResponse(
            alerts=alerts,
            total_alerts=len(alerts),
            high_risk=sum(1 for a in alerts if a.risk == "High"),
            medium_risk=sum(1 for a in alerts if a.risk == "Medium"),
            low_risk=sum(1 for a in alerts if a.risk == "Low"),
            zap_url=settings.zap_base_url,
        )
    except httpx.HTTPError as exc:
        logger.exception("ZAP request failed")
        raise HTTPException(status_code=502, detail=f"OWASP ZAP unreachable: {exc}") from exc
