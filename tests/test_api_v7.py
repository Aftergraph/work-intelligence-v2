"""Tests for API versioning, pagination, and advanced features."""

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
        yield c, app


def _ingest_obs(client, tenant="test", text=None):
    if text is None:
        text = f"Unik observation {uuid.uuid4().hex[:8]} skal købes hurtigt"
    return client.post(
        "/v1/observations",
        json={
            "tenant_id": tenant,
            "source": "test",
            "text": text,
            "external_id": f"ext-{uuid.uuid4().hex[:8]}",
        },
        headers={"Authorization": "Bearer test-token"},
    )


class TestAPIVersioning:
    """Tests for API version information."""

    def test_version_endpoint(self, client):
        c, _ = client
        resp = c.get("/v1/version", headers={"Authorization": "Bearer test-token"})
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert "features" in data

    def test_healthz_includes_version(self, client):
        c, _ = client
        resp = c.get("/healthz")
        data = resp.json()
        assert "version" in data
        assert "api_version" in data

    def test_openapi_spec_available(self, client):
        c, _ = client
        resp = c.get("/openapi.json")
        assert resp.status_code == 200
        data = resp.json()
        assert "openapi" in data
        assert "paths" in data


class TestPagination:
    """Tests for list endpoint pagination."""

    def test_list_with_limit(self, client):
        c, _ = client
        # Create multiple items
        for _ in range(5):
            _ingest_obs(c, tenant="pagination")

        resp = c.get(
            "/v1/work-items",
            params={"tenant_id": "pagination", "limit": 3},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] <= 3

    def test_list_default_limit(self, client):
        c, _ = client
        _ingest_obs(c, tenant="pagination-default")

        resp = c.get(
            "/v1/work-items",
            params={"tenant_id": "pagination-default"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200

    def test_list_empty_tenant(self, client):
        c, _ = client
        resp = c.get(
            "/v1/work-items",
            params={"tenant_id": "empty-tenant"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0


class TestSearchFunctionality:
    """Tests for search endpoint."""

    def test_search_by_title(self, client):
        c, _ = client
        _ingest_obs(c, text="Køb nye computere til kontoret hurtigt")
        _ingest_obs(c, text="Send faktura til kunden straks")

        resp = c.get(
            "/v1/search",
            params={"q": "computere", "tenant_id": "test"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1

    def test_search_no_results(self, client):
        c, _ = client
        resp = c.get(
            "/v1/search",
            params={"q": "nonexistent", "tenant_id": "test"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0

    def test_search_requires_query(self, client):
        c, _ = client
        resp = c.get(
            "/v1/search",
            params={"tenant_id": "test"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 422


class TestBulkOperations:
    """Tests for bulk operations."""

    def test_bulk_status(self, client):
        c, _ = client
        ids = []
        for _ in range(3):
            resp = _ingest_obs(c, tenant="bulk")
            ids.append(resp.json()["work_item"]["id"])

        resp = c.post(
            "/v1/work-items/bulk-status",
            json={"work_item_ids": ids, "tenant_id": "bulk"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 3

    def test_bulk_status_nonexistent(self, client):
        c, _ = client
        resp = c.post(
            "/v1/work-items/bulk-status",
            json={"work_item_ids": ["nonexistent"], "tenant_id": "test"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"][0]["status"] == "NOT_FOUND"


class TestTransitionHistory:
    """Tests for transition history."""

    def test_transitions_after_approve(self, client):
        c, _ = client
        resp = _ingest_obs(c)
        item_id = resp.json()["work_item"]["id"]

        # Approve
        c.post(
            f"/v1/work-items/{item_id}/review",
            json={"action": "approve", "actor": "reviewer@test.dk"},
            params={"tenant_id": "test"},
            headers={"Authorization": "Bearer test-token"},
        )

        # Get transitions
        resp = c.get(
            f"/v1/work-items/{item_id}/transitions",
            params={"tenant_id": "test"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["transitions"]) >= 1

    def test_transitions_not_found(self, client):
        c, _ = client
        resp = c.get(
            "/v1/work-items/nonexistent/transitions",
            params={"tenant_id": "test"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 404


class TestPublicationHistory:
    """Tests for publication history."""

    def test_publications_empty(self, client):
        c, _ = client
        resp = _ingest_obs(c)
        item_id = resp.json()["work_item"]["id"]

        resp = c.get(
            f"/v1/work-items/{item_id}/publications",
            params={"tenant_id": "test"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["publications"]) == 0


class TestTenantListing:
    """Tests for tenant listing."""

    def test_list_tenants(self, client):
        c, _ = client
        _ingest_obs(c, tenant="tenant-x")
        _ingest_obs(c, tenant="tenant-y")

        resp = c.get(
            "/v1/tenants",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tenants"]) >= 2


class TestMetricsEndpoint:
    """Tests for metrics endpoint."""

    def test_metrics_returns_data(self, client):
        c, _ = client
        resp = c.get(
            "/v1/metrics",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "count_by_action" in data
        assert "total_observations" in data


class TestMonitoringEndpoint:
    """Tests for monitoring endpoint."""

    def test_monitoring_returns_system_metrics(self, client):
        c, _ = client
        resp = c.get(
            "/v1/monitoring",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "system" in data
        assert "service" in data
        assert "timestamp" in data
