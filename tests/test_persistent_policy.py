"""Tests for persistent tenant policy storage."""

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


class TestPersistentPolicyStorage:
    """Test persistent tenant policy storage."""

    def test_upsert_and_get_policy(self, client):
        # Create policy
        resp = client.post("/v1/tenants/test-tenant/policy", params={
            "allowed_sources": ["conversation", "email"],
            "allow_works": True,
            "max_work_items": 50,
            "max_priority": "high",
        }, headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] is True
        assert data["persisted"] is True

        # Verify in-memory
        resp = client.get("/v1/tenants/test-tenant/policy", headers=AUTH)
        assert resp.status_code == 200
        policy = resp.json()
        assert set(policy["allowed_sources"]) == {"conversation", "email"}
        assert policy["allow_works"] is True
        assert policy["max_work_items"] == 50

    def test_list_persisted_policies(self, client):
        # Create two policies
        client.post("/v1/tenants/tenant-a/policy", params={
            "allowed_sources": ["conversation"],
            "allow_works": True,
        }, headers=AUTH)

        client.post("/v1/tenants/tenant-b/policy", params={
            "allowed_sources": ["email"],
            "allow_works": False,
        }, headers=AUTH)

        # List all
        resp = client.get("/v1/tenants/policies", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        tenant_ids = {p["tenant_id"] for p in data["policies"]}
        assert "tenant-a" in tenant_ids
        assert "tenant-b" in tenant_ids

    def test_delete_persisted_policy(self, client):
        # Create policy
        client.post("/v1/tenants/test-tenant/policy", params={
            "allowed_sources": ["conversation"],
            "allow_works": True,
        }, headers=AUTH)

        # Delete it
        resp = client.delete("/v1/tenants/test-tenant/policy", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # Verify it's gone from persisted list
        resp = client.get("/v1/tenants/policies", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_delete_nonexistent_policy(self, client):
        resp = client.delete("/v1/tenants/nonexistent/policy", headers=AUTH)
        assert resp.status_code == 404

    def test_update_policy_merges_with_existing(self, client):
        # Create initial policy
        client.post("/v1/tenants/test-tenant/policy", params={
            "allowed_sources": ["conversation"],
            "allow_works": False,
            "max_work_items": 100,
        }, headers=AUTH)

        # Update only some fields
        resp = client.post("/v1/tenants/test-tenant/policy", params={
            "allow_works": True,
        }, headers=AUTH)
        assert resp.status_code == 200

        # Verify merge
        resp = client.get("/v1/tenants/test-tenant/policy", headers=AUTH)
        assert resp.status_code == 200
        policy = resp.json()
        assert policy["allow_works"] is True
        assert policy["max_work_items"] == 100  # preserved from initial

    def test_policy_survives_restart(self, client):
        # Create policy
        client.post("/v1/tenants/test-tenant/policy", params={
            "allowed_sources": ["conversation"],
            "allow_works": True,
        }, headers=AUTH)

        # Verify persisted
        resp = client.get("/v1/tenants/policies", headers=AUTH)
        assert resp.json()["count"] == 1

    def test_list_policies_empty(self, client):
        resp = client.get("/v1/tenants/policies", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_upsert_policy_all_fields(self, client):
        resp = client.post("/v1/tenants/full-tenant/policy", params={
            "allowed_sources": ["conversation", "email", "calendar"],
            "allowed_destinations": ["renos", "works"],
            "max_work_items": 200,
            "max_priority": "critical",
            "allow_works": True,
            "dedupe_threshold": 0.85,
            "auto_create_work_items": False,
            "require_approval_for_promotion": False,
        }, headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["persisted"] is True

        # Verify all fields persisted
        resp = client.get("/v1/tenants/policies", headers=AUTH)
        assert resp.status_code == 200
        policies = resp.json()["policies"]
        assert len(policies) == 1
        p = policies[0]
        assert p["tenant_id"] == "full-tenant"
        assert set(p["allowed_sources"]) == {"conversation", "email", "calendar"}
        assert p["allowed_destinations"] == ["renos", "works"]
        assert p["max_work_items"] == 200
        assert p["max_priority"] == "critical"
        assert p["allow_works"] is True
        assert p["dedupe_threshold"] == 0.85
        assert p["auto_create_work_items"] is False
        assert p["require_approval_for_promotion"] is False
