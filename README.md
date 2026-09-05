# Aftergraph Work Intelligence V2

Source-neutral automatic work detection. Humans keep working in conversation, email, calendars, RenOS, code, or other systems; adapters send observations here; the engine creates or updates canonical work automatically.

> **V2 status:** production integration. Real source adapters, tenant policies, review/approval flow, destination publishers (RenOS + WORKS), provenance/evidence, observability, and end-to-end tests. Built TDD-first against the actual Aftergraph contracts.

## Core invariant

**Work creates tickets. Humans do not create tickets.**

The canonical object is a `WorkItem`, not a destination-specific ticket:

```text
Signal → Observation → WorkCandidate → Resolution → WorkItem
       → Review/Approve → Publication → optional WORKS promotion → Evidence
```

A WorkItem can later publish to RenOS, Linear, Jira, GitHub, or an execution system without changing the inference core.

## What V2 adds over V1

| Pillar | Capability |
|---|---|
| 1 | **Source adapters** — `conversation`, `email`, `calendar`, `code`, `renos` (job-lifecycle signals). Each is a pure transformer of a source payload into canonical `ObservationInput` with full provenance. |
| 2 | **Tenant policies** — per-tenant source allowlist, auto-create on/off, work-item quota, priority cap, dedupe threshold, and WORKS-promotion gate. |
| 3 | **Review/approval flow** — strict state machine (`OPEN → APPROVED/REJECTED/SNOOZED/CANCELLED → PUBLISHED/PROMOTED_TO_WORKS`) with a durable, append-only transition audit. |
| 4 | **Destination publishers** — `RenosPublisher` (Project-Renos `Job`), `WorksPublisher` (works-execution `Work`, conforming to `work.schema/1.0`), plus V1's `WebhookPublisher`. A `PublishRouter` enforces per-tenant destination allowlists. |
| 5 | **End-to-end tests** — the full canonical path against in-process RenOS/WORKS fakes (real HTTP round-trips, no mocks as final evidence). |
| 6 | **Provenance/evidence** — HMAC-SHA256 evidence envelope mirroring Aftergraph L2 (`evidence.schema/1.1`), plus metrics and structured JSON logging. |
| 7 | **API surface** — review, promote, metrics, and evidence endpoints. |

## The separation that matters

`WorkItem` (canonical) is **not** `WORKS Work` (executable). Promotion to execution requires:

1. The tenant's policy has `allow_works=True` (opt-in, default off), **and**
2. The work-item is in `APPROVED` status (explicit human review), **and**
3. An explicit `promote` call with an actor.

The engine never auto-promotes. Every promotion is audited.

## Quick start

```bash
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
pytest

aftergraph-work-intelligence --db ./data/work-intelligence.db
```

OpenAPI is available at `http://127.0.0.1:8087/docs`.

## API

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/healthz` | Health/version |
| `POST` | `/v1/observations` | Ingest source-neutral observation and auto-resolve work |
| `GET` | `/v1/work-items?tenant_id=...` | List canonical work |
| `GET` | `/v1/work-items/{id}?tenant_id=...` | Work + supporting observations + publication receipts |
| `POST` | `/v1/work-items/{id}/review` | Approve / reject / snooze / cancel |
| `POST` | `/v1/work-items/{id}/promote` | Explicit WORKS promotion (policy-gated) |
| `POST` | `/v1/work-items/{id}/publish?tenant_id=...` | Publish to a configured destination |
| `GET` | `/v1/work-items/{id}/evidence?tenant_id=...` | Provenance/evidence envelope |
| `GET` | `/v1/metrics` | Observability counters |
| `GET` | `/v1/monitoring` | System metrics (CPU, memory, disk) |
| `GET` | `/v1/version` | Version and feature flags |

## Authentication

Set `AFTERGRAPH_API_TOKEN`. When configured, `/v1/*` requires:

```text
Authorization: Bearer ***
```

An unset token is intentionally local-development mode. Production deployments should set a token and place the service behind the existing Aftergraph trust/control boundary.

## Tenant policies

Policies are held in-memory by the running service (persistent loading is a follow-up). Configure them programmatically:

```python
from aftergraph_work_intelligence.policy import PolicyStore, TenantPolicy

policy_store = PolicyStore()
policy_store.put("renos", TenantPolicy(
    allowed_sources={"conversation", "email", "calendar", "renos"},
    allowed_destinations={"renos", "works"},
    allow_works=True,          # opt-in to WORKS promotion
    max_work_items=100,
    max_priority="high",
))
```

## Destination publishers

Destinations are configured by the operator, not supplied as arbitrary URLs by clients:

```python
from aftergraph_work_intelligence.publishers import (
    RenosPublisher, WorksPublisher, build_publish_router,
)

router = build_publish_router(
    {
        "renos": RenosPublisher(base_url="http://renos:3000", company_id="company-123"),
        "works": WorksPublisher(base_url="http://works:8080"),
    },
    policy_store=policy_store,
)
```

## Evidence

Every work-item can emit a content-addressed evidence envelope:

```bash
curl http://127.0.0.1:8087/v1/work-items/{id}/evidence?tenant_id=renos
```

The envelope is HMAC-SHA256 over the canonical `(tenant_id, work_item_id, title, canonical_key, observations[])` tuple, keyed by `AFTERGRAPH_EVIDENCE_SECRET`. A downstream auditor can verify the digest without works-execution being online.

## Development

```bash
pytest -q          # 136 tests
```

The test suite is TDD-first: every V2 module has a failing test before implementation, and the end-to-end tests run the full canonical path against in-process RenOS/WORKS fakes (real HTTP, no mocks as final evidence).
