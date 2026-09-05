"""Tests for API v0.3.0 features: rate limiting, request ID, version endpoint."""

import pytest
from fastapi.testclient import TestClient

from src.aftergraph_work_intelligence.api import create_app


@pytest.fixture()
def client():
    app = create_app(
        db_path=":memory:",
        api_token=None,
        evidence_secret="test-secret",
    )
    with TestClient(app) as c:
        yield c


class TestVersionEndpoint:
    def test_version_returns_info(self, client: TestClient):
        resp = client.get("/v1/version")
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == "0.2.0"
        assert data["build"] == "production"
        assert data["status"] == "active"
        assert "adapters" in data["features"]
        assert "policies" in data["features"]
        assert "transitions" in data["features"]
        assert "publishers" in data["features"]
        assert "evidence" in data["features"]
        assert "metrics" in data["features"]


class TestRequestID:
    def test_request_id_auto_generated(self, client: TestClient):
        resp = client.get("/v1/version")
        assert resp.status_code == 200
        assert "X-Request-ID" in resp.headers
        # Should be a UUID
        req_id = resp.headers["X-Request-ID"]
        assert len(req_id) == 36  # UUID format

    def test_request_id_from_header(self, client: TestClient):
        custom_id = "my-custom-request-id"
        resp = client.get("/v1/version", headers={"X-Request-ID": custom_id})
        assert resp.status_code == 200
        assert resp.headers["X-Request-ID"] == custom_id


class TestRateLimiter:
    def test_allows_normal_requests(self, client: TestClient):
        for _ in range(10):
            resp = client.get("/v1/version")
            assert resp.status_code == 200

    def test_rate_limit_exceeded(self, client: TestClient):
        # Make 60 requests (default limit)
        for _ in range(60):
            resp = client.get("/v1/version")
            assert resp.status_code == 200

        # 61st request should be rate limited
        resp = client.get("/v1/version")
        assert resp.status_code == 429
        assert "Rate limit exceeded" in resp.json()["detail"]


class TestCORSMiddleware:
    def test_cors_headers(self, client: TestClient):
        resp = client.options(
            "/v1/version",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers
        # CORS reflects the origin (not wildcard when Origin is present)
        assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"


class TestHealthz:
    def test_healthz_returns_ok(self, client: TestClient):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "aftergraph-work-intelligence"
        assert data["version"] == "0.2.0"
