"""Dump FastAPI OpenAPI schema to stdout. Side-effect-free — does NOT boot lifespan."""

import json
import sys

from app.main import app


def main() -> None:
    spec = app.openapi()
    json.dump(spec, sys.stdout, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
