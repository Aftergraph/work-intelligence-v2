# Aftergraph Work Intelligence V1

Source-neutral automatic work detection. Humans keep working in conversation, email, calendars, RenOS, code, or other systems; adapters send observations here; the engine creates or updates canonical work automatically.

> **V1 status:** working local reference implementation. Deterministic extraction baseline, SQLite persistence, cross-source dedupe, observation provenance, tenant isolation, optional bearer auth, and publish-anywhere webhooks are implemented and tested.

## Core invariant

**Work creates tickets. Humans do not create tickets.**

The canonical object is a `WorkItem`, not a destination-specific ticket:

```text
Signal → Observation → WorkCandidate → Resolution → WorkItem → Publication
```

A WorkItem can later publish to RenOS, Linear, Jira, GitHub, or an execution system without changing the inference core.

## What V1 actually does

- Accepts observations from any source through `POST /v1/observations`.
- Detects explicit Danish and English commitments/requests with a deterministic baseline extractor.
- Persists every observation, including non-actionable ones.
- Creates WorkItems automatically for actionable observations.
- Replays `(tenant, source, external_id)` idempotently.
- Merges related observations conservatively within the same tenant.
- Never merges across tenants.
- Keeps every supporting Observation attached to the WorkItem.
- Lists and retrieves canonical WorkItems.
- Publishes a WorkItem to operator-configured webhook destinations on demand and persists the publication receipt.
- Uses no LLM credentials in the trusted V1 core. A model extractor can later implement the same extraction interface.

## Quick start

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest

aftergraph-work-intelligence --db ./data/work-intelligence.db
```

OpenAPI is available at `http://127.0.0.1:8087/docs`.

### Create work from an observation

```bash
curl -X POST http://127.0.0.1:8087/v1/observations \
  -H 'Content-Type: application/json' \
  -d '{
    "tenant_id":"renos",
    "source":"conversation",
    "external_id":"voice-2026-09-05-001",
    "text":"Vi skal købe parfumefri rengøringsmidler før mandag"
  }'
```

The response includes `action: created`, the durable Observation, and the automatically created WorkItem.

### Send the same obligation from another source

```bash
curl -X POST http://127.0.0.1:8087/v1/observations \
  -H 'Content-Type: application/json' \
  -d '{
    "tenant_id":"renos",
    "source":"gmail",
    "external_id":"gmail-msg-123",
    "text":"Husk at købe parfumefri rengøringsmidler før mandag"
  }'
```

When the canonical token similarity clears the conservative merge threshold, the response is `action: merged` and `observation_count` increments instead of creating another ticket-shaped object.

## API

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/healthz` | Health/version |
| `POST` | `/v1/observations` | Ingest source-neutral observation and auto-resolve work |
| `GET` | `/v1/work-items?tenant_id=...` | List canonical work |
| `GET` | `/v1/work-items/{id}?tenant_id=...` | Work + supporting observations + publication receipts |
| `POST` | `/v1/work-items/{id}/publish?tenant_id=...` | Publish to a configured destination |

## Authentication

Set `AFTERGRAPH_API_TOKEN`. When configured, `/v1/*` requires:

```text
Authorization: Bearer <token>
```

An unset token is intentionally local-development mode. Production deployments should set a token and place the service behind the existing Aftergraph trust/control boundary.

## Publish-anywhere

Destinations are configured by the operator, not supplied as arbitrary URLs by clients:

```bash
export AFTERGRAPH_PUBLISHERS_JSON='{"renos":"https://renos.example/api/work-items"}'
export AFTERGRAPH_WEBHOOK_SECRET='shared-hmac-secret'
```

Then:

```bash
curl -X POST 'http://127.0.0.1:8087/v1/work-items/wi_x/publish?tenant_id=renos' \
  -H 'Content-Type: application/json' \
  -d '{"destination":"renos"}'
```

Webhook bodies use `schema: aftergraph.work-item/1.0`; when a secret is configured the request carries `X-Aftergraph-Signature: sha256=<hmac>`.

## Boundaries

V1 deliberately does **not**:

- read Gmail or Google Calendar credentials itself;
- hard-code Rendetalje vocabulary into the core;
- turn detected text directly into an executable WORKS mission;
- infer exact dates or owners when source evidence does not support them;
- claim production-grade NLP recall/precision before field evidence exists.

Gmail, Calendar, voice/chat, RenOS, and codebase integrations are adapters that normalize events into the Observation contract. This keeps the core publishable instead of becoming a Jonas-shaped ball of integrations held together by optimism.

## Tests

```bash
pytest -q
```

Current local verification: **15/15 passing** on 5 September 2026.

See `docs/research/AUTONOMOUS-WORK-DETECTION.md` for the research hypothesis and 30-day dogfood evaluation protocol.
