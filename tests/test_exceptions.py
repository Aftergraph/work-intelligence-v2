"""Tests for custom exceptions."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aftergraph_work_intelligence.api import create_app
from aftergraph_work_intelligence.exceptions import (
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
    ObservationError,
    PolicyViolationError,
    RateLimitError,
    ValidationError,
    WorkIntelligenceError,
)


@pytest.fixture()
def client(tmp_path):
    db = tmp_path / "test.db"
    app = create_app(db_path=db, api_token="test-token")
    with TestClient(app) as c:
        yield c


AUTH = {"Authorization": "Bearer test-token"}


class TestCustomExceptions:
    """Test custom exception classes."""

    def test_base_exception(self):
        exc = WorkIntelligenceError("custom message")
        assert exc.detail == "custom message"
        assert exc.status_code == 500

    def test_observation_error(self):
        exc = ObservationError("bad observation")
        assert exc.status_code == 422
        assert "bad observation" in exc.detail

    def test_policy_violation(self):
        exc = PolicyViolationError()
        assert exc.status_code == 403

    def test_not_found(self):
        exc = NotFoundError("item not here")
        assert exc.status_code == 404

    def test_rate_limit(self):
        exc = RateLimitError()
        assert exc.status_code == 429

    def test_auth_error(self):
        exc = AuthenticationError()
        assert exc.status_code == 401

    def test_authorization_error(self):
        exc = AuthorizationError()
        assert exc.status_code == 403

    def test_validation_error(self):
        exc = ValidationError()
        assert exc.status_code == 422


class TestExceptionHandler:
    """Test global exception handler in API."""

    def test_404_returns_proper_format(self, client):
        resp = client.get("/v1/nonexistent", headers=AUTH)
        assert resp.status_code == 404

    def test_unauthorized_returns_401(self, client):
        resp = client.get("/v1/work-items?tenant_id=default")
        assert resp.status_code == 401
        data = resp.json()
        assert "detail" in data
