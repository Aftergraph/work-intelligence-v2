# Work Intelligence V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use test-driven development for every behavior change.

**Goal:** Deliver a running source-neutral observation-to-WorkItem service that creates/merges work automatically and can publish it to configured destinations.

**Architecture:** FastAPI wraps a deterministic extraction/resolution service backed by SQLite. The work inference core is tenant-neutral and keeps source provenance. Destination publication is a separate adapter boundary.

**Tech Stack:** Python 3.11+, FastAPI 0.128, Pydantic 2.13, stdlib sqlite3, pytest, httpx/TestClient.

**Spec:** `docs/spec/2026-09-05-work-intelligence-v1-design.md`

## Global Constraints

- No hard-coded RenOS business rules in core.
- No LLM credential required for V1.
- No cross-tenant merge.
- No arbitrary request-controlled publish URL.
- Every created/merged WorkItem keeps its supporting Observation.
- Detection does not grant execution authority.

---

### Task 1: Extraction baseline

**Files:** `tests/test_extractor.py`, `src/aftergraph_work_intelligence/models.py`, `src/aftergraph_work_intelligence/extractor.py`

- [x] Write tests for Danish commitment, missing obligation, completed statement, English urgent follow-up, and non-actionable text.
- [x] Run tests and observe missing-module failure.
- [x] Implement minimal deterministic extractor and canonical token model.
- [x] Run extractor tests: 5 passing.

### Task 2: Durable resolution

**Files:** `tests/test_service.py`, `src/aftergraph_work_intelligence/store.py`, `src/aftergraph_work_intelligence/service.py`

- [x] Write tests for create, replay idempotency, cross-source merge, tenant isolation, observation-only persistence, and provenance detail.
- [x] Implement SQLite schema and transactional WorkItem/link writes.
- [x] Implement conservative resolver and create/merge lifecycle.
- [x] Run service tests: 6 passing.

### Task 3: HTTP and publication surface

**Files:** `tests/test_api.py`, `src/aftergraph_work_intelligence/api.py`, `src/aftergraph_work_intelligence/publishers.py`

- [x] Write API tests for ingestion/list/detail, validation, auth, and publisher receipt persistence.
- [x] Implement FastAPI lifecycle and routes.
- [x] Implement configured destination publisher interface and HMAC webhook publisher.
- [x] Run API tests: 4 passing.

### Task 4: Product packaging and verification

**Files:** `README.md`, `.env.example`, `Dockerfile`, `pyproject.toml`, research/design docs.

- [x] Document API, boundaries, local run and publication configuration.
- [x] Add container packaging.
- [x] Run full test suite: 15/15 passing.
- [x] Boot live Uvicorn server and verify health, automatic creation, and listing against SQLite.
