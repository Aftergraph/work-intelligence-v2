from __future__ import annotations

import uuid

from .extractor import RuleExtractor
from .models import (
    IngestResult,
    Observation,
    ObservationInput,
    WorkItem,
    WorkItemDetail,
    utc_now,
)
from .policy import PolicyStore
from .store import SQLiteStore


class WorkIntelligenceService:
    def __init__(
        self,
        store: SQLiteStore,
        extractor: RuleExtractor | None = None,
        dedupe_threshold: float = 0.72,
        policy_store: PolicyStore | None = None,
    ):
        self.store = store
        self.extractor = extractor or RuleExtractor()
        self.default_dedupe_threshold = dedupe_threshold
        self.policy_store = policy_store or PolicyStore()

    @property
    def policy_store(self) -> PolicyStore:
        return self._policy_store

    @policy_store.setter
    def policy_store(self, value: PolicyStore) -> None:
        self._policy_store = value

    def _dedupe_threshold(self, tenant_id: str) -> float:
        return self.policy_store.get(tenant_id).dedupe_threshold or self.default_dedupe_threshold

    def ingest(self, payload: ObservationInput) -> IngestResult:
        tenant_id = payload.tenant_id.strip()
        source = payload.source.strip().casefold()
        text = payload.text.strip()
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not source:
            raise ValueError("source is required")
        if not text:
            raise ValueError("text is required")

        policy = self.policy_store.get(tenant_id)

        if payload.external_id:
            replay = self.store.get_observation_by_external(tenant_id, source, payload.external_id)
            if replay is not None:
                item = self.store.get_work_item_for_observation(replay.id)
                return IngestResult(action="replayed", observation=replay, work_item=item)

        now = utc_now()
        observation = Observation(
            id=f"obs_{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            source=source,
            external_id=payload.external_id,
            actor=payload.actor,
            text=text,
            metadata=dict(payload.metadata),
            occurred_at=payload.occurred_at or now,
            created_at=now,
        )
        self.store.create_observation(observation)

        # Source allowlist gate
        if not policy.allows_source(source):
            return IngestResult(action="observed", observation=observation, work_item=None)

        normalized_payload = ObservationInput(
            tenant_id=tenant_id,
            source=source,
            text=text,
            external_id=payload.external_id,
            actor=payload.actor,
            occurred_at=payload.occurred_at,
            metadata=payload.metadata,
            title_hint=payload.title_hint,
            owner_hint=payload.owner_hint,
            due_hint=payload.due_hint,
            priority_hint=policy.cap_priority(payload.priority_hint) if payload.priority_hint else None,
        )
        candidate = self.extractor.extract(normalized_payload)
        if candidate is None:
            return IngestResult(action="observed", observation=observation, work_item=None)

        # Auto-create gate
        if not policy.auto_create_work_items:
            return IngestResult(action="observed", observation=observation, work_item=None)

        # Quota gate — count only OPEN work-items per tenant
        if policy.max_work_items > 0:
            existing_open = self.store.count_open_work_items(tenant_id)
            existing_key = self.store.get_work_item_by_canonical_key(tenant_id, candidate.canonical_key)
            if existing_key is None and existing_open >= policy.max_work_items:
                return IngestResult(action="observed", observation=observation, work_item=None)

        existing = self._resolve(
            tenant_id,
            candidate.canonical_key,
            candidate.canonical_tokens,
            threshold=self._dedupe_threshold(tenant_id),
        )
        if existing is not None:
            # Apply priority cap to the merged candidate
            capped_priority = policy.cap_priority(candidate.priority)
            capped_candidate = (
                candidate
                if capped_priority == candidate.priority
                else _replace_candidate_priority(candidate, capped_priority)
            )
            merged = self.store.merge_work_item(existing, capped_candidate, observation.id, updated_at=now)
            return IngestResult(action="merged", observation=observation, work_item=merged)

        capped_priority = policy.cap_priority(candidate.priority)
        item = WorkItem(
            id=f"wi_{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            title=candidate.title,
            summary=candidate.summary,
            status="OPEN",
            priority=capped_priority,
            owner=candidate.owner,
            due_hint=candidate.due_hint,
            next_action=candidate.next_action,
            confidence=candidate.confidence,
            canonical_key=candidate.canonical_key,
            canonical_tokens=candidate.canonical_tokens,
            observation_count=1,
            created_at=now,
            updated_at=now,
        )
        self.store.create_work_item(item, observation.id)
        return IngestResult(action="created", observation=observation, work_item=item)

    def _resolve(
        self,
        tenant_id: str,
        canonical_key: str,
        canonical_tokens: tuple[str, ...],
        threshold: float | None = None,
    ) -> WorkItem | None:
        threshold = threshold if threshold is not None else self.default_dedupe_threshold
        best: WorkItem | None = None
        best_score = 0.0
        for item in self.store.list_open_work_items(tenant_id):
            if item.canonical_key == canonical_key:
                return item
            score = self.extractor.similarity(canonical_tokens, item.canonical_tokens)
            if score >= threshold and score > best_score:
                best = item
                best_score = score
        return best

    def list_work_items(self, tenant_id: str, limit: int = 100) -> list[WorkItem]:
        return self.store.list_work_items(tenant_id, limit)

    def get_observation(self, observation_id: str) -> Observation | None:
        return self.store.get_observation(observation_id)

    def get_work_item_detail(self, work_item_id: str, tenant_id: str) -> WorkItemDetail:
        item = self.store.get_work_item(work_item_id, tenant_id=tenant_id)
        if item is None:
            raise KeyError(work_item_id)
        return WorkItemDetail(
            work_item=item,
            observations=self.store.observations_for_work_item(work_item_id),
            publications=self.store.publications_for_work_item(work_item_id),
        )


def _replace_candidate_priority(candidate, new_priority: str):
    from dataclasses import replace

    return replace(candidate, priority=new_priority)
