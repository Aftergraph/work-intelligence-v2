"""Tests for API usage tracking."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aftergraph_work_intelligence.api import create_app


@pytest.fixture()
def client(tmp_path):
    db = tmp_path / "test.db"
    app = create_app(db_path=db, api_token="test-token")
    with TestClient(app) as c:
        yield c


AUTH = {"Authorization": "Bearer test-token"}


class TestUsageTracking:
    """Test API usage statistics endpoint."""

    def test_usage_endpoint_exists(self, client):
        resp = client.get("/v1/usage", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "total_requests" in data
        assert "by_path" in data
        assert "by_status" in data

    def test_usage_tracks_requests(self, client):
        # Make some requests
        client.get("/v1/usage", headers=AUTH)
        client.get("/v1/work-items?tenant_id=default", headers=AUTH)
        client.get("/v1/tenants", headers=AUTH)

        resp = client.get("/v1/usage", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_requests"] >= 3

    def test_usage_tracks_errors(self, client):
        # Trigger a 404
        client.get("/v1/work-items/nonexistent?tenant_id=default", headers=AUTH)

        resp = client.get("/v1/usage", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_errors"] >= 1

    def test_usage_by_path(self, client):
        client.get("/v1/usage", headers=AUTH)

        resp = client.get("/v1/usage", headers=AUTH)
        data = resp.json()
        assert "/v1/usage" in data["by_path"]

    def test_usage_by_status(self, client):
        client.get("/v1/usage", headers=AUTH)

        resp = client.get("/v1/usage", headers=AUTH)
        data = resp.json()
        assert "200" in data["by_status"]
