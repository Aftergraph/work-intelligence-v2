FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY pyproject.toml README.md ./

# Install Python dependencies
RUN pip install --no-cache-dir -e ".[dev]"

# Copy source code
COPY src/ ./src/
COPY tests/ ./tests/
COPY openapi.json ./

# Expose port
EXPOSE 8299

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8299/healthz')" || exit 1

# Run API
CMD ["uvicorn", "aftergraph_work_intelligence.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8299"]
