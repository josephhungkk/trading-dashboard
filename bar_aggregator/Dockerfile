FROM python:3.14-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml /app/
RUN uv pip install --system --no-cache .
# Source files import as `from bar_aggregator.app.X` so the on-disk layout
# must be /app/bar_aggregator/app/* with /app on PYTHONPATH (default WORKDIR
# is on sys.path in `python -m`). Create an empty package marker for
# bar_aggregator/ since the source tree omits it (the build context's root
# is bar_aggregator/ itself, no parent __init__.py to copy).
RUN mkdir -p /app/bar_aggregator && touch /app/bar_aggregator/__init__.py
COPY app/ /app/bar_aggregator/app/
RUN adduser --no-create-home --disabled-password --uid 1001 aggregator
USER aggregator
EXPOSE 9100
CMD ["python", "-m", "bar_aggregator.app.main"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9100/healthz')" || exit 1
