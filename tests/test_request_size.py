"""Tests for request size limiting."""

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


class TestRequestSizeLimit:
    """Test request body size limiting."""

    def test_normal_request_accepted(self, client):
        resp = client.post("/v1/observations", json={
            "tenant_id": "default",
            "source": "test",
            "text": "small payload",
        }, headers=AUTH)
        assert resp.status_code in (200, 201, 202)

    def test_large_payload_rejected(self, client):
        # Create a large payload (11MB)
        large_text = "x" * (11 * 1024 * 1024)
        resp = client.post("/v1/observations", json={
            "tenant_id": "default",
            "source": "test",
            "text": large_text,
        }, headers=AUTH, content=large_text.encode())
        # Should be rejected or the client should error
        assert resp.status_code in (413, 422, 400)
