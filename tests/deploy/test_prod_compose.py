"""Phase 5b R7: backend must run single-worker uvicorn until Phase 9
introduces Redis SETNX leader election for the consumer streams + SSE."""
from pathlib import Path

import yaml


def test_prod_backend_runs_single_worker():
    compose_path = Path(__file__).parent.parent.parent / "docker-compose.prod.yml"
    with open(compose_path) as f:
        compose = yaml.safe_load(f)
    backend_cmd = compose["services"]["backend"]["command"]
    assert "--workers 1" in backend_cmd or "--workers=1" in backend_cmd, (
        "Phase 5b requires single-worker uvicorn (R7). "
        "Multi-worker consumer/SSE leadership is Phase 9 work."
    )


def test_prod_backend_has_graceful_shutdown_30s():
    compose_path = Path(__file__).parent.parent.parent / "docker-compose.prod.yml"
    with open(compose_path) as f:
        compose = yaml.safe_load(f)
    backend_cmd = compose["services"]["backend"]["command"]
    assert "--timeout-graceful-shutdown 30" in backend_cmd, (
        "R9: lifespan teardown needs 30s drain to let in-flight POST /orders complete"
    )
