from pathlib import Path

from aftergraph_work_intelligence.models import ObservationInput
from aftergraph_work_intelligence.service import WorkIntelligenceService
from aftergraph_work_intelligence.store import SQLiteStore


def make_service(tmp_path: Path) -> WorkIntelligenceService:
    return WorkIntelligenceService(SQLiteStore(tmp_path / "work-intelligence.db"))


def test_actionable_observation_creates_work_item(tmp_path):
    service = make_service(tmp_path)
    result = service.ingest(ObservationInput(
        tenant_id="renos",
        source="conversation",
        external_id="chat-1",
        text="Vi skal sende kunden en bekræftelse før mandag",
    ))
    assert result.action == "created"
    assert result.work_item is not None
    assert result.work_item.observation_count == 1


def test_external_id_replay_is_idempotent(tmp_path):
    service = make_service(tmp_path)
    payload = ObservationInput(
        tenant_id="renos",
        source="gmail",
        external_id="msg-123",
        text="Husk at sende kunden en bekræftelse",
    )
    first = service.ingest(payload)
    second = service.ingest(payload)
    assert second.action == "replayed"
    assert second.observation.id == first.observation.id
    assert second.work_item.id == first.work_item.id
    assert len(service.list_work_items("renos")) == 1


def test_same_tenant_related_observations_merge(tmp_path):
    service = make_service(tmp_path)
    first = service.ingest(ObservationInput(
        tenant_id="renos", source="conversation", text="Vi skal sende kunden en bekræftelse"
    ))
    second = service.ingest(ObservationInput(
        tenant_id="renos", source="gmail", text="Husk at sende bekræftelse til kunden"
    ))
    assert first.work_item is not None
    assert second.action == "merged"
    assert second.work_item.id == first.work_item.id
    assert second.work_item.observation_count == 2


def test_cross_tenant_observations_never_merge(tmp_path):
    service = make_service(tmp_path)
    a = service.ingest(ObservationInput(
        tenant_id="tenant-a", source="conversation", text="Vi skal sende kunden en bekræftelse"
    ))
    b = service.ingest(ObservationInput(
        tenant_id="tenant-b", source="conversation", text="Vi skal sende kunden en bekræftelse"
    ))
    assert a.work_item.id != b.work_item.id


def test_non_actionable_observation_is_persisted_without_ticket(tmp_path):
    service = make_service(tmp_path)
    result = service.ingest(ObservationInput(
        tenant_id="renos", source="conversation", external_id="chat-info", text="Kunden bor i Aarhus"
    ))
    assert result.action == "observed"
    assert result.work_item is None
    assert service.get_observation(result.observation.id) is not None


def test_work_item_detail_contains_supporting_observations(tmp_path):
    service = make_service(tmp_path)
    result = service.ingest(ObservationInput(
        tenant_id="renos", source="conversation", text="Vi skal ringe til kunden i morgen"
    ))
    detail = service.get_work_item_detail(result.work_item.id, tenant_id="renos")
    assert detail.work_item.id == result.work_item.id
    assert len(detail.observations) == 1
    assert "ringe" in detail.observations[0].text.lower()
