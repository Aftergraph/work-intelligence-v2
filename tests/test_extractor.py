from aftergraph_work_intelligence.extractor import RuleExtractor
from aftergraph_work_intelligence.models import ObservationInput


def obs(text: str) -> ObservationInput:
    return ObservationInput(tenant_id="tenant-a", source="conversation", text=text)


def test_extracts_explicit_danish_commitment():
    candidate = RuleExtractor().extract(obs("Vi skal sende kunden en bekræftelse før mandag"))
    assert candidate is not None
    assert "bekræftelse" in candidate.title.lower()
    assert candidate.priority == "medium"
    assert candidate.due_hint == "før mandag"
    assert candidate.confidence >= 0.85


def test_extracts_missing_obligation_without_explicit_imperative():
    candidate = RuleExtractor().extract(obs("Kunden mangler stadig en bekræftelse"))
    assert candidate is not None
    assert candidate.confidence >= 0.70


def test_ignores_completed_statement():
    candidate = RuleExtractor().extract(obs("Kundens bekræftelse er sendt og opgaven er færdig"))
    assert candidate is None


def test_extracts_english_followup_and_critical_priority():
    candidate = RuleExtractor().extract(obs("URGENT: we need to follow up with the customer tomorrow"))
    assert candidate is not None
    assert candidate.priority == "critical"
    assert candidate.due_hint == "tomorrow"


def test_non_actionable_information_is_ignored():
    candidate = RuleExtractor().extract(obs("Kunden bor i Aarhus og har to etager"))
    assert candidate is None
