from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Observation, Publication, WorkCandidate, WorkItem

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS intake_observations (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT,
    actor TEXT,
    text TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_intake_observation_external
ON intake_observations(tenant_id, source, external_id)
WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS intake_work_items (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    owner TEXT,
    due_hint TEXT,
    next_action TEXT NOT NULL,
    confidence REAL NOT NULL,
    canonical_key TEXT NOT NULL,
    canonical_tokens_json TEXT NOT NULL,
    observation_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intake_work_items_tenant_status
ON intake_work_items(tenant_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_intake_work_items_key
ON intake_work_items(tenant_id, canonical_key);

CREATE TABLE IF NOT EXISTS intake_work_item_observations (
    work_item_id TEXT NOT NULL REFERENCES intake_work_items(id) ON DELETE CASCADE,
    observation_id TEXT NOT NULL UNIQUE REFERENCES intake_observations(id) ON DELETE CASCADE,
    linked_at TEXT NOT NULL,
    PRIMARY KEY(work_item_id, observation_id)
);

CREATE TABLE IF NOT EXISTS intake_publications (
    id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL REFERENCES intake_work_items(id) ON DELETE CASCADE,
    destination TEXT NOT NULL,
    external_id TEXT,
    response_json TEXT NOT NULL,
    published_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intake_publications_work
ON intake_publications(work_item_id, published_at DESC);
"""


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SQLiteStore:
    """Durable store for observations, canonical work items, and publish receipts."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA busy_timeout=5000")
            self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def create_observation(self, observation: Observation) -> None:
        with self._lock:
            self._db.execute(
                """
                INSERT INTO intake_observations
                (id, tenant_id, source, external_id, actor, text, metadata_json, occurred_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.id,
                    observation.tenant_id,
                    observation.source,
                    observation.external_id,
                    observation.actor,
                    observation.text,
                    json.dumps(observation.metadata, ensure_ascii=False, sort_keys=True),
                    _iso(observation.occurred_at),
                    _iso(observation.created_at),
                ),
            )

    def get_observation(self, observation_id: str) -> Observation | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM intake_observations WHERE id = ?", (observation_id,)
            ).fetchone()
        return self._observation(row) if row else None

    def get_observation_by_external(self, tenant_id: str, source: str, external_id: str) -> Observation | None:
        with self._lock:
            row = self._db.execute(
                """
                SELECT * FROM intake_observations
                WHERE tenant_id = ? AND source = ? AND external_id = ?
                """,
                (tenant_id, source, external_id),
            ).fetchone()
        return self._observation(row) if row else None

    def create_work_item(self, item: WorkItem, observation_id: str) -> None:
        now = _iso(item.created_at)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    """
                    INSERT INTO intake_work_items
                    (id, tenant_id, title, summary, status, priority, owner, due_hint, next_action,
                     confidence, canonical_key, canonical_tokens_json, observation_count, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.id,
                        item.tenant_id,
                        item.title,
                        item.summary,
                        item.status,
                        item.priority,
                        item.owner,
                        item.due_hint,
                        item.next_action,
                        item.confidence,
                        item.canonical_key,
                        json.dumps(item.canonical_tokens, ensure_ascii=False),
                        item.observation_count,
                        now,
                        _iso(item.updated_at),
                    ),
                )
                self._db.execute(
                    """
                    INSERT INTO intake_work_item_observations(work_item_id, observation_id, linked_at)
                    VALUES (?, ?, ?)
                    """,
                    (item.id, observation_id, now),
                )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def merge_work_item(self, item: WorkItem, candidate: WorkCandidate, observation_id: str, updated_at: datetime) -> WorkItem:
        priority_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        priority = candidate.priority if priority_rank.get(candidate.priority, 1) > priority_rank.get(item.priority, 1) else item.priority
        owner = item.owner or candidate.owner
        due_hint = item.due_hint or candidate.due_hint
        confidence = max(item.confidence, candidate.confidence)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    """
                    UPDATE intake_work_items
                    SET priority = ?, owner = ?, due_hint = ?, next_action = ?, confidence = ?,
                        observation_count = observation_count + 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (priority, owner, due_hint, candidate.next_action, confidence, _iso(updated_at), item.id),
                )
                self._db.execute(
                    """
                    INSERT INTO intake_work_item_observations(work_item_id, observation_id, linked_at)
                    VALUES (?, ?, ?)
                    """,
                    (item.id, observation_id, _iso(updated_at)),
                )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        merged = self.get_work_item(item.id)
        assert merged is not None
        return merged

    def list_open_work_items(self, tenant_id: str, limit: int = 200) -> list[WorkItem]:
        with self._lock:
            rows = self._db.execute(
                """
                SELECT * FROM intake_work_items
                WHERE tenant_id = ? AND status NOT IN ('DONE', 'CANCELLED')
                ORDER BY updated_at DESC LIMIT ?
                """,
                (tenant_id, max(1, min(limit, 1000))),
            ).fetchall()
        return [self._work_item(row) for row in rows]

    def count_open_work_items(self, tenant_id: str) -> int:
        with self._lock:
            row = self._db.execute(
                """
                SELECT COUNT(*) AS n FROM intake_work_items
                WHERE tenant_id = ? AND status NOT IN ('DONE', 'CANCELLED')
                """,
                (tenant_id,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def get_work_item_by_canonical_key(self, tenant_id: str, canonical_key: str) -> WorkItem | None:
        with self._lock:
            row = self._db.execute(
                """
                SELECT * FROM intake_work_items
                WHERE tenant_id = ? AND canonical_key = ?
                """,
                (tenant_id, canonical_key),
            ).fetchone()
        return self._work_item(row) if row else None

    def list_work_items(self, tenant_id: str, limit: int = 100) -> list[WorkItem]:
        with self._lock:
            rows = self._db.execute(
                """
                SELECT * FROM intake_work_items
                WHERE tenant_id = ? ORDER BY updated_at DESC LIMIT ?
                """,
                (tenant_id, max(1, min(limit, 1000))),
            ).fetchall()
        return [self._work_item(row) for row in rows]

    def get_work_item(self, work_item_id: str, tenant_id: str | None = None) -> WorkItem | None:
        sql = "SELECT * FROM intake_work_items WHERE id = ?"
        params: tuple[Any, ...] = (work_item_id,)
        if tenant_id is not None:
            sql += " AND tenant_id = ?"
            params = (work_item_id, tenant_id)
        with self._lock:
            row = self._db.execute(sql, params).fetchone()
        return self._work_item(row) if row else None

    def get_work_item_for_observation(self, observation_id: str) -> WorkItem | None:
        with self._lock:
            row = self._db.execute(
                """
                SELECT w.* FROM intake_work_items w
                JOIN intake_work_item_observations l ON l.work_item_id = w.id
                WHERE l.observation_id = ?
                """,
                (observation_id,),
            ).fetchone()
        return self._work_item(row) if row else None

    def observations_for_work_item(self, work_item_id: str) -> list[Observation]:
        with self._lock:
            rows = self._db.execute(
                """
                SELECT o.* FROM intake_observations o
                JOIN intake_work_item_observations l ON l.observation_id = o.id
                WHERE l.work_item_id = ? ORDER BY o.occurred_at ASC
                """,
                (work_item_id,),
            ).fetchall()
        return [self._observation(row) for row in rows]

    def save_publication(self, publication: Publication) -> None:
        with self._lock:
            self._db.execute(
                """
                INSERT INTO intake_publications
                (id, work_item_id, destination, external_id, response_json, published_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    publication.id,
                    publication.work_item_id,
                    publication.destination,
                    publication.external_id,
                    json.dumps(publication.response, ensure_ascii=False, sort_keys=True),
                    _iso(publication.published_at),
                ),
            )

    def publications_for_work_item(self, work_item_id: str) -> list[Publication]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM intake_publications WHERE work_item_id = ? ORDER BY published_at ASC",
                (work_item_id,),
            ).fetchall()
        return [self._publication(row) for row in rows]

    @staticmethod
    def _observation(row: sqlite3.Row) -> Observation:
        return Observation(
            id=row["id"],
            tenant_id=row["tenant_id"],
            source=row["source"],
            external_id=row["external_id"],
            actor=row["actor"],
            text=row["text"],
            metadata=json.loads(row["metadata_json"] or "{}"),
            occurred_at=_dt(row["occurred_at"]),
            created_at=_dt(row["created_at"]),
        )

    @staticmethod
    def _work_item(row: sqlite3.Row) -> WorkItem:
        return WorkItem(
            id=row["id"],
            tenant_id=row["tenant_id"],
            title=row["title"],
            summary=row["summary"],
            status=row["status"],
            priority=row["priority"],
            owner=row["owner"],
            due_hint=row["due_hint"],
            next_action=row["next_action"],
            confidence=float(row["confidence"]),
            canonical_key=row["canonical_key"],
            canonical_tokens=tuple(json.loads(row["canonical_tokens_json"] or "[]")),
            observation_count=int(row["observation_count"]),
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    @staticmethod
    def _publication(row: sqlite3.Row) -> Publication:
        return Publication(
            id=row["id"],
            work_item_id=row["work_item_id"],
            destination=row["destination"],
            external_id=row["external_id"],
            response=json.loads(row["response_json"] or "{}"),
            published_at=_dt(row["published_at"]),
        )
