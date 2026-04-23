"""Prometheus /metrics endpoint, gated by admin auth."""

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core import metrics as metrics_module
from app.core.deps import require_admin_jwt

router = APIRouter(dependencies=[Depends(require_admin_jwt)])


@router.get("/metrics")
async def get_metrics() -> Response:
    data = generate_latest(metrics_module.registry)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
