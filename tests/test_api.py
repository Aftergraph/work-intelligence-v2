from fastapi.testclient import TestClient

from aftergraph_work_intelligence.api import create_app
from aftergraph_work_intelligence.publishers import Publisher, PublishReceipt


class RecordingPublisher(Publisher):
    def __init__(self):
        self.calls = []

    def publish(self, destination, work_item, observations):
        self.calls.append((destination, work_item.id))
        return PublishReceipt(destination=destination, external_id="renos-42", response={"ok": True})


def test_post_observation_creates_and_lists_work(tmp_path):
    app = create_app(db_path=tmp_path / "api.db")
    with TestClient(app) as client:
        response = client.post("/v1/observations", json={
            "tenant_id": "renos",
            "source": "conversation",
            "external_id": "voice-1",
            "text": "Vi skal købe parfumefri rengøringsmidler før mandag",
        })
        assert response.status_code == 201
        body = response.json()
        assert body["action"] == "created"
        work_id = body["work_item"]["id"]

        listed = client.get("/v1/work-items", params={"tenant_id": "renos"})
        assert listed.status_code == 200
        assert listed.json()["count"] == 1

        detail = client.get(f"/v1/work-items/{work_id}", params={"tenant_id": "renos"})
        assert detail.status_code == 200
        assert len(detail.json()["observations"]) == 1


def test_api_rejects_missing_tenant(tmp_path):
    app = create_app(db_path=tmp_path / "api.db")
    with TestClient(app) as client:
        response = client.post("/v1/observations", json={"source": "conversation", "text": "Vi skal ringe"})
        assert response.status_code == 422


def test_optional_token_auth(tmp_path):
    app = create_app(db_path=tmp_path / "api.db", api_token="secret-token")
    with TestClient(app) as client:
        denied = client.get("/v1/work-items", params={"tenant_id": "renos"})
        assert denied.status_code == 401
        allowed = client.get(
            "/v1/work-items",
            params={"tenant_id": "renos"},
            headers={"Authorization": "Bearer secret-token"},
        )
        assert allowed.status_code == 200


def test_publish_endpoint_uses_destination_adapter_and_persists_receipt(tmp_path):
    publisher = RecordingPublisher()
    app = create_app(db_path=tmp_path / "api.db", publisher=publisher)
    with TestClient(app) as client:
        created = client.post("/v1/observations", json={
            "tenant_id": "renos",
            "source": "conversation",
            "text": "Vi skal sende kunden en bekræftelse",
        }).json()
        work_id = created["work_item"]["id"]
        published = client.post(
            f"/v1/work-items/{work_id}/publish",
            params={"tenant_id": "renos"},
            json={"destination": "renos"},
        )
        assert published.status_code == 201
        assert published.json()["external_id"] == "renos-42"
        assert publisher.calls == [("renos", work_id)]

        detail = client.get(f"/v1/work-items/{work_id}", params={"tenant_id": "renos"}).json()
        assert len(detail["publications"]) == 1
