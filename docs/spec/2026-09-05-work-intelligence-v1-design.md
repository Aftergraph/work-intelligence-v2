# Work Intelligence V1 Design

**Date:** 2026-09-05  
**Status:** Approved by product owner through repeated explicit build instructions in the originating design conversation.

## Purpose

Build a reusable, tenant-neutral work-detection layer that turns ambient operational observations into canonical work automatically. RenOS/Rendetalje is the first dogfood tenant, not the domain model.

## Architecture

```text
connectors/adapters
      ↓
Observation API
      ↓
deterministic Extractor V1
      ↓
WorkCandidate
      ↓
Resolver (tenant boundary + conservative similarity)
      ↓
SQLite canonical WorkItem + provenance links
      ↓
configured Publisher adapter
```

The inference layer never grants execution authority. Publishing to a consequential runtime is a separate boundary.

## Components

- `models.py`: source-neutral domain records.
- `extractor.py`: deterministic Danish/English baseline and canonicalization.
- `store.py`: SQLite persistence and provenance relations.
- `service.py`: idempotency, resolution, create/merge behavior.
- `publishers.py`: destination-neutral publisher interface plus configured HMAC webhook publisher.
- `api.py`: FastAPI/OpenAPI boundary, optional bearer auth, lifecycle resource management.

## Data laws

1. Every actionable WorkItem has at least one durable Observation.
2. Non-actionable observations may exist without WorkItems.
3. `(tenant_id, source, external_id)` is idempotent when `external_id` exists.
4. Dedupe cannot cross tenant boundaries.
5. False merge is treated as more dangerous than duplicate creation; threshold is conservative.
6. A destination URL is operator configuration, never user-controlled request data.
7. Detected work is not automatically executable work.

## V1 extraction

Rule-based extraction is intentionally the baseline condition. A later LLM extractor must return the same `WorkCandidate` contract and will be measured against this baseline. Uncertain deadlines remain `due_hint` text instead of fabricated timestamps.

## Acceptance

- Automatic Danish/English explicit-work capture.
- Persist/resolve/list/detail API.
- Idempotent replay.
- Same-tenant cross-source merge.
- Cross-tenant isolation.
- Provenance links in detail response.
- Optional bearer auth.
- On-demand destination publication with durable receipt.
- Automated tests and live local API smoke test.
