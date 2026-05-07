FROM python:3.14-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml /app/
RUN uv pip install --system --no-cache .
COPY app/ /app/app/
EXPOSE 9100
CMD ["python", "-m", "app.main"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9100/healthz')" || exit 1
