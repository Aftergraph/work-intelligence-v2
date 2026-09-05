"""Tests for OpenTelemetry tracing."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from aftergraph_work_intelligence.api import create_app
from aftergraph_work_intelligence.tracing import setup_tracing


@pytest.fixture()
def client(tmp_path):
    db = tmp_path / "test.db"
    app = create_app(db_path=db, api_token="test-token")
    with TestClient(app) as c:
        yield c


AUTH = {"Authorization": "Bearer test-token"}


class TestTracing:
    """Test tracing configuration."""

    def test_tracing_disabled_by_default(self):
        """Tracing should be disabled when OTEL_ENABLED is not set."""
        # Ensure env is clean
        os.environ.pop("OTEL_ENABLED", None)
        from starlette.applications import Starlette
        app = Starlette()
        # Should not raise
        setup_tracing(app)

    def test_tracing_enabled_with_console(self, monkeypatch):
        """Tracing should work with console exporter."""
        monkeypatch.setenv("OTEL_ENABLED", "true")
        monkeypatch.setenv("OTEL_EXPORTER_TYPE", "console")
        from starlette.applications import Starlette
        app = Starlette()
        # Should not raise
        setup_tracing(app)

    def test_tracing_enabled_otlp(self, monkeypatch):
        """Tracing should try OTLP exporter."""
        monkeypatch.setenv("OTEL_ENABLED", "true")
        monkeypatch.setenv("OTEL_EXPORTER_TYPE", "otlp")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        from starlette.applications import Starlette
        app = Starlette()
        # Should not raise (may fail to connect but shouldn't crash)
        setup_tracing(app)


class TestTracingAPI:
    """Test that API works with tracing enabled."""

    def test_api_works_without_tracing(self, client):
        """API should work fine without tracing enabled."""
        resp = client.get("/healthz")
        assert resp.status_code == 200

    def test_healthz_returns_ok(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
