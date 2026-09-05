"""Tests for audit trail."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aftergraph_work_intelligence.api import create_app
from aftergraph_work_intelligence.audit import AuditLog


@pytest.fixture()
def client(tmp_path):
    db = tmp_path / "test.db"
    app = create_app(db_path=db, api_token="test-token")
    with TestClient(app) as c:
        yield c


AUTH = {"Authorization": "Bearer test-token"}


class TestAuditLog:
    """Test AuditLog class directly."""

    def test_record_and_query(self):
        log = AuditLog()
        log.record("test.event", actor="user-1", target="item-1")
        log.record("test.event", actor="user-2", target="item-2")

        entries = log.query()
        assert len(entries) == 2
        assert entries[0]["event"] == "test.event"

    def test_query_by_event(self):
        log = AuditLog()
        log.record("create", actor="a", target="x")
        log.record("delete", actor="b", target="y")
        log.record("create", actor="c", target="z")

        creates = log.query(event="create")
        assert len(creates) == 2

    def test_query_by_actor(self):
        log = AuditLog()
        log.record("op", actor="alice", target="x")
        log.record("op", actor="bob", target="y")

        alice_ops = log.query(actor="alice")
        assert len(alice_ops) == 1

    def test_query_by_target(self):
        log = AuditLog()
        log.record("op", actor="a", target="key:123")
        log.record("op", actor="b", target="item:456")

        key_ops = log.query(target="key")
        assert len(key_ops) == 1

    def test_max_entries(self):
        log = AuditLog(max_entries=3)
        for i in range(5):
            log.record("event", actor="a", target=f"t{i}")
        assert log.count() == 3

    def test_count(self):
        log = AuditLog()
        log.record("a", actor="x", target="y")
        log.record("b", actor="x", target="y")
        assert log.count() == 2
        assert log.count(event="a") == 1


class TestAuditAPI:
    """Test audit API endpoints."""

    def test_audit_stats(self, client):
        resp = client.get("/v1/audit/stats", headers=AUTH)
        assert resp.status_code == 200
        assert "total_entries" in resp.json()

    def test_audit_query(self, client):
        resp = client.get("/v1/audit", headers=AUTH)
        assert resp.status_code == 200
        assert "entries" in resp.json()

    def test_create_key_creates_audit_entry(self, client):
        client.post("/v1/api-keys", json={"name": "audit-test"}, headers=AUTH)
        resp = client.get("/v1/audit?event=api_key.created", headers=AUTH)
        entries = resp.json()["entries"]
        assert len(entries) >= 1
        assert entries[-1]["event"] == "api_key.created"

    def test_revoke_key_creates_audit_entry(self, client):
        resp = client.post("/v1/api-keys", json={"name": "revoke-audit"}, headers=AUTH)
        key_id = resp.json()["id"]
        client.delete(f"/v1/api-keys/{key_id}", headers=AUTH)

        resp = client.get("/v1/audit?event=api_key.revoked", headers=AUTH)
        entries = resp.json()["entries"]
        assert len(entries) >= 1

    def test_rotate_key_creates_audit_entry(self, client):
        resp = client.post("/v1/api-keys", json={"name": "rotate-audit"}, headers=AUTH)
        key_id = resp.json()["id"]
        client.post(f"/v1/api-keys/{key_id}/rotate", headers=AUTH)

        resp = client.get("/v1/audit?event=api_key.rotated", headers=AUTH)
        entries = resp.json()["entries"]
        assert len(entries) >= 1
