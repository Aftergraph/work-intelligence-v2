"""Tests for API version headers."""

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


class TestVersionHeaders:
    """Test API version headers in responses."""

    def test_version_headers_present(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert "X-API-Version" in resp.headers
        assert resp.headers["X-API-Version"] == "v1"
        assert "X-App-Version" in resp.headers
        assert resp.headers["X-App-Version"] == "0.2.0"

    def test_version_headers_on_api_endpoints(self, client):
        resp = client.get("/v1/work-items?tenant_id=default", headers=AUTH)
        assert resp.headers["X-API-Version"] == "v1"

    def test_request_id_header(self, client):
        resp = client.get("/healthz")
        assert "X-Request-ID" in resp.headers
        assert len(resp.headers["X-Request-ID"]) > 0

    def test_custom_request_id(self, client):
        resp = client.get("/healthz", headers={"X-Request-ID": "my-custom-id"})
        assert resp.headers["X-Request-ID"] == "my-custom-id"
