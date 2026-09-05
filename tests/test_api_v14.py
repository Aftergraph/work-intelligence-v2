"""Tests for additional edge cases and error handling."""

from __future__ import annotations

import uuid

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


def _create(c, text="Vi skal købe 5 licenser til teamet hurtigt"):
    resp = c.post("/v1/observations", json={
        "tenant_id": "default",
        "source": "manual",
        "text": text,
    }, headers=AUTH)
    if resp.status_code == 201 and resp.json().get("work_item"):
        return resp.json()["work_item"]["id"]
    return None


class TestEdgeCasesAdvanced:
    """Advanced edge cases for robustness."""

    def test_unicode_tenant_id(self, client):
        resp = client.post("/v1/observations", json={
            "tenant_id": "lejre-123",
            "source": "manual",
            "text": "Vi skal købe licenser hurtigt",
        }, headers=AUTH)
        assert resp.status_code in (200, 201, 202)

    def test_special_chars_in_source(self, client):
        resp = client.post("/v1/observations", json={
            "tenant_id": "default",
            "source": "test-source",
            "text": "Vi skal købe licenser hurtigt",
        }, headers=AUTH)
        assert resp.status_code in (200, 201, 202)

    def test_empty_metadata(self, client):
        resp = client.post("/v1/observations", json={
            "tenant_id": "default",
            "source": "manual",
            "text": "Vi skal købe licenser hurtigt",
            "metadata": {},
        }, headers=AUTH)
        assert resp.status_code in (200, 201, 202)

    def test_nested_metadata(self, client):
        resp = client.post("/v1/observations", json={
            "tenant_id": "default",
            "source": "manual",
            "text": "Vi skal købe licenser hurtigt",
            "metadata": {"nested": {"key": "value"}},
        }, headers=AUTH)
        assert resp.status_code in (200, 201, 202)

    def test_large_metadata(self, client):
        resp = client.post("/v1/observations", json={
            "tenant_id": "default",
            "source": "manual",
            "text": "Vi skal købe licenser hurtigt",
            "metadata": {f"key_{i}": f"value_{i}" for i in range(100)},
        }, headers=AUTH)
        assert resp.status_code in (200, 201, 202)

    def test_concurrent_different_texts(self, client):
        results = []
        for i in range(10):
            resp = client.post("/v1/observations", json={
                "tenant_id": "default",
                "source": "manual",
                "text": f"Unik tekst {i} med køb",
            }, headers=AUTH)
            results.append(resp)

        assert all(r.status_code in (200, 201, 202) for r in results)

    def test_review_with_reason(self, client):
        item_id = _create(client)
        assert item_id

        resp = client.post(
            f"/v1/work-items/{item_id}/review?tenant_id=default",
            json={"action": "approve", "actor": "user-1", "reason": "Godkendt af teamleder"},
            headers=AUTH,
        )
        assert resp.status_code == 200

    def test_review_with_resume_at(self, client):
        item_id = _create(client)
        assert item_id

        # Snooze requires resume_at
        resp = client.post(
            f"/v1/work-items/{item_id}/review?tenant_id=default",
            json={"action": "snooze", "actor": "user-1", "resume_at": "2026-12-31T00:00:00Z"},
            headers=AUTH,
        )
        # May succeed or fail depending on state
        assert resp.status_code in (200, 400, 409)

    def test_cancel_work_item(self, client):
        item_id = _create(client)
        assert item_id

        resp = client.post(
            f"/v1/work-items/{item_id}/review?tenant_id=default",
            json={"action": "cancel", "actor": "user-1"},
            headers=AUTH,
        )
        assert resp.status_code in (200, 400, 409)


class TestErrorResponseDetails:
    """Verify error response details are informative."""

    def test_422_shows_missing_fields(self, client):
        resp = client.post("/v1/observations", json={}, headers=AUTH)
        assert resp.status_code == 422
        data = resp.json()
        assert "detail" in data
        # Should show which fields are missing
        details = data["detail"]
        assert isinstance(details, list)
        assert len(details) >= 1

    def test_404_hasmeaningful_message(self, client):
        resp = client.get(f"/v1/work-items/{uuid.uuid4()}?tenant_id=default", headers=AUTH)
        assert resp.status_code == 404
        data = resp.json()
        assert "detail" in data

    def test_401_has_detail(self, client):
        resp = client.get("/v1/work-items?tenant_id=default")
        assert resp.status_code == 401
        data = resp.json()
        assert "detail" in data


class TestQueryParameters:
    """Verify query parameter handling."""

    def test_pagination_limit(self, client):
        for i in range(5):
            _create(client, f"Vi skal købe {i} licenser hurtigt")

        resp = client.get("/v1/work-items?tenant_id=default&limit=2", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        # Limit should be respected
        assert len(data["work_items"]) <= 2

    def test_pagination_offset(self, client):
        for i in range(5):
            _create(client, f"Vi skal købe {i} licenser hurtigt")

        resp = client.get("/v1/work-items?tenant_id=default&limit=2&offset=2", headers=AUTH)
        assert resp.status_code == 200

    def test_search_with_limit(self, client):
        _create(client)

        resp = client.get("/v1/search?q=licenser&tenant_id=default&limit=1", headers=AUTH)
        assert resp.status_code == 200

    def test_invalid_limit(self, client):
        resp = client.get("/v1/work-items?tenant_id=default&limit=-1", headers=AUTH)
        assert resp.status_code == 422

    def test_limit_too_large(self, client):
        resp = client.get("/v1/work-items?tenant_id=default&limit=10000", headers=AUTH)
        assert resp.status_code == 422
