from fastapi import APIRouter

from app.api.v1 import (
    analysis,
    approvals,
    auth,
    builds,
    github,
    infrastructure,
    metrics,
    pipelines,
    security,
    webhook,
    sql,
    hook,
)

router = APIRouter()

router.include_router(auth.router,            prefix="/auth",           tags=["auth"])
router.include_router(github.router,          prefix="/github",         tags=["github"])
router.include_router(analysis.router,        prefix="/analysis",       tags=["analysis"])
router.include_router(pipelines.router,       prefix="/pipelines",      tags=["pipelines"])
router.include_router(metrics.router,         prefix="/metrics",        tags=["metrics"])
router.include_router(security.router,        prefix="/security",       tags=["security"])
router.include_router(infrastructure.router,  prefix="/infrastructure", tags=["infrastructure"])
router.include_router(builds.router,          prefix="/builds",         tags=["builds"])
router.include_router(approvals.router,       prefix="/approvals",      tags=["approvals"])
router.include_router(webhook.router,         prefix="/webhooks",       tags=["webhooks"])
router.include_router(sql.router,             prefix="/sql",            tags=["sql"])
router.include_router(hook.router,            prefix="/hook",           tags=["hook"])