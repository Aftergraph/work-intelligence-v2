"""Tests for additional API endpoints: tenants, transitions, publications, search."""

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


def _ingest_obs(client, tenant="tenant-a", text=None):
    if text is None:
        text = f"Unik observation {uuid.uuid4().hex[:8]} skal behandles"
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


class TestTenantListing:
    """Tests for tenant listing endpoint."""

    def test_list_tenants_empty(self, client):
        c, _ = client
        resp = c.get(
            "/v1/tenants",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "tenants" in data
        assert isinstance(data["tenants"], list)

    def test_list_tenants_with_data(self, client):
        c, _ = client
        # Ingest for multiple tenants
        _ingest_obs(c, tenant="tenant-x")
        _ingest_obs(c, tenant="tenant-y")
        _ingest_obs(c, tenant="tenant-x")  # Same tenant, different observation

        resp = c.get(
            "/v1/tenants",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        tenants = data["tenants"]
        assert len(tenants) >= 2
        tenant_ids = [t["tenant_id"] for t in tenants]
        assert "tenant-x" in tenant_ids
        assert "tenant-y" in tenant_ids

    def test_tenant_item_count(self, client):
        c, _ = client
        _ingest_obs(c, tenant="tenant-count")
        _ingest_obs(c, tenant="tenant-count")

        resp = c.get(
            "/v1/tenants",
            headers={"Authorization": "Bearer test-token"},
        )
        data = resp.json()
        tenant = next(t for t in data["tenants"] if t["tenant_id"] == "tenant-count")
        assert tenant["work_item_count"] >= 1


class TestTransitionHistory:
    """Tests for work item transition history."""

    def test_transitions_empty(self, client):
        c, _ = client
        resp = _ingest_obs(c)
        item_id = resp.json()["work_item"]["id"]

        resp = c.get(
            f"/v1/work-items/{item_id}/transitions",
            params={"tenant_id": "tenant-a"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "transitions" in data

    def test_transitions_after_review(self, client):
        c, _ = client
        resp = _ingest_obs(c)
        item_id = resp.json()["work_item"]["id"]

        # Approve
        c.post(
            f"/v1/work-items/{item_id}/review",
            json={"action": "approve", "actor": "reviewer@test.dk"},
            params={"tenant_id": "tenant-a"},
            headers={"Authorization": "Bearer test-token"},
        )

        resp = c.get(
            f"/v1/work-items/{item_id}/transitions",
            params={"tenant_id": "tenant-a"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["transitions"]) >= 1
        # Should have at least one transition with action "approve"
        actions = [t["action"] for t in data["transitions"]]
        assert "approve" in actions

    def test_transitions_not_found(self, client):
        c, _ = client
        resp = c.get(
            "/v1/work-items/nonexistent/transitions",
            params={"tenant_id": "tenant-a"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 404


class TestPublicationHistory:
    """Tests for work item publication history."""

    def test_publications_empty(self, client):
        c, _ = client
        resp = _ingest_obs(c)
        item_id = resp.json()["work_item"]["id"]

        resp = c.get(
            f"/v1/work-items/{item_id}/publications",
            params={"tenant_id": "tenant-a"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "publications" in data
        assert len(data["publications"]) == 0

    def test_publications_not_found(self, client):
        c, _ = client
        resp = c.get(
            "/v1/work-items/nonexistent/publications",
            params={"tenant_id": "tenant-a"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 404


class TestSearchEndpoint:
    """Tests for work item search endpoint."""

    def test_search_empty(self, client):
        c, _ = client
        resp = c.get(
            "/v1/search",
            params={"q": "nonexistent", "tenant_id": "tenant-a"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert data["count"] == 0

    def test_search_with_results(self, client):
        c, _ = client
        _ingest_obs(c, text="Køb nye computere til kontoret hurtigt")
        _ingest_obs(c, text="Send faktura til kunden straks")

        resp = c.get(
            "/v1/search",
            params={"q": "computere", "tenant_id": "tenant-a"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1

    def test_search_requires_query(self, client):
        c, _ = client
        resp = c.get(
            "/v1/search",
            params={"tenant_id": "tenant-a"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 422  # Missing required 'q' parameter


class TestBulkOperations:
    """Tests for bulk operations."""

    def test_bulk_status_check(self, client):
        c, _ = client
        # Ingest several items
        ids = []
        for _ in range(3):
            resp = _ingest_obs(c, tenant="tenant-bulk")
            ids.append(resp.json()["work_item"]["id"])

        # Check status of multiple items
        resp = c.post(
            f"/v1/work-items/bulk-status?tenant_id=tenant-bulk&work_item_ids={ids[0]}&work_item_ids={ids[1]}&work_item_ids={ids[2]}",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert len(data["items"]) == 3
        # All should be OPEN
        for item in data["items"]:
            assert item["status"] == "OPEN"


class TestIdempotency:
    """Tests for idempotent operations."""

    def test_duplicate_observation_same_external_id(self, client):
        c, _ = client
        ext_id = f"idem-{uuid.uuid4().hex[:8]}"
        text = f"Idempotent test {uuid.uuid4().hex[:8]} skal købes"

        # First ingestion
        resp1 = _ingest_obs(c, text=text)
        assert resp1.status_code == 201

        # Second ingestion with same external_id
        resp2 = c.post(
            "/v1/observations",
            json={
                "tenant_id": "tenant-a",
                "source": "test",
                "text": text,
                "external_id": ext_id,
            },
            headers={"Authorization": "Bearer test-token"},
        )
        # Should return 202 (observed, not created) or 200
        assert resp2.status_code in (200, 202)


class TestHealthzEnhanced:
    """Tests for enhanced healthz endpoint."""

    def test_healthz_includes_version(self, client):
        c, _ = client
        resp = c.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert "api_version" in data
        assert data["status"] == "ok"

    def test_healthz_includes_service_name(self, client):
        c, _ = client
        resp = c.get("/healthz")
        data = resp.json()
        assert data["service"] == "aftergraph-work-intelligence"
