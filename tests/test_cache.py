"""Tests for caching layer."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from aftergraph_work_intelligence.api import create_app
from aftergraph_work_intelligence.cache import Cache


@pytest.fixture()
def client(tmp_path):
    db = tmp_path / "test.db"
    app = create_app(db_path=db, api_token="test-token")
    with TestClient(app) as c:
        yield c


AUTH = {"Authorization": "Bearer test-token"}


class TestCache:
    """Test Cache class directly."""

    def test_set_and_get(self):
        c = Cache()
        c.set("key1", "value1")
        assert c.get("key1") == "value1"

    def test_get_missing_key(self):
        c = Cache()
        assert c.get("nonexistent") is None

    def test_ttl_expiration(self):
        c = Cache()
        c.set("short", "data", ttl=0.1)
        assert c.get("short") == "data"
        time.sleep(0.2)
        assert c.get("short") is None

    def test_delete(self):
        c = Cache()
        c.set("key", "val")
        assert c.delete("key") is True
        assert c.get("key") is None
        assert c.delete("nonexistent") is False

    def test_clear(self):
        c = Cache()
        c.set("a", 1)
        c.set("b", 2)
        cleared = c.clear()
        assert cleared == 2
        assert c.get("a") is None

    def test_max_size_eviction(self):
        c = Cache(max_size=3)
        c.set("a", 1)
        c.set("b", 2)
        c.set("c", 3)
        c.set("d", 4)  # Should evict
        assert c.stats()["size"] <= 3

    def test_stats(self):
        c = Cache()
        c.set("x", 1)
        c.get("x")  # hit
        c.get("y")  # miss
        stats = c.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1

    def test_decorator(self):
        c = Cache()
        call_count = 0

        @c.cached(ttl=60)
        def expensive(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        assert expensive(5) == 10
        assert expensive(5) == 10
        assert call_count == 1  # Called only once


class TestCacheAPI:
    """Test cache API endpoints."""

    def test_cache_stats(self, client):
        resp = client.get("/v1/cache/stats", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "size" in data
        assert "hits" in data
        assert "misses" in data

    def test_cache_clear(self, client):
        resp = client.post("/v1/cache/clear", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_cache_delete(self, client):
        resp = client.delete("/v1/cache/nonexistent", headers=AUTH)
        assert resp.status_code == 404
