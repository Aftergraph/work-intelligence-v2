FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AFTERGRAPH_HOST=0.0.0.0 \
    AFTERGRAPH_PORT=8087 \
    AFTERGRAPH_DB=/data/work-intelligence.db

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 aftergraph && mkdir -p /data && chown -R aftergraph:aftergraph /data /app
USER aftergraph

EXPOSE 8087
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8087/healthz', timeout=2).read()"

CMD ["aftergraph-work-intelligence", "--host", "0.0.0.0", "--port", "8087", "--db", "/data/work-intelligence.db"]
