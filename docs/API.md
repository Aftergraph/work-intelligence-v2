# Aftergraph Work Intelligence V2 — API Reference

## Base URL

```
http://localhost:8087
```

## Authentication

All endpoints (except `/healthz`) require Bearer token authentication:

```bash
curl -H "Authorization: Bearer YOUR_API_TOKEN" http://localhost:8087/v1/observations
```

Set the token via environment variable:

```bash
export AFTERGRAPH_API_TOKEN="your-secret-token"
```

## Rate Limiting

API requests are rate-limited to 60 requests per minute per IP address.

Configure via environment variable:

```bash
export AFTERGRAPH_RATE_LIMIT=120  # requests per minute
```

Rate limit exceeded returns HTTP 429:

```json
{
  "detail": "Rate limit exceeded. Try again later."
}
```

## Request ID Tracking

Every request includes an `X-Request-ID` header for tracing:

```bash
curl -v http://localhost:8087/v1/version 2>&1 | grep X-Request-ID
# < X-Request-ID: 550e8400-e29b-41d4-a716-446655440000
```

You can provide your own request ID:

```bash
curl -H "X-Request-ID: my-request-123" http://localhost:8087/v1/version
```

---

## Endpoints

### Health Check

```
GET /healthz
```

Returns service health status. No authentication required.

**Response:**

```json
{
  "status": "ok",
  "service": "aftergraph-work-intelligence",
  "version": "0.2.0"
}
```

---

### Version Information

```
GET /v1/version
```

Returns service version and feature flags.

**Response:**

```json
{
  "version": "0.2.0",
  "build": "production",
  "status": "active",
  "features": [
    "adapters",
    "policies",
    "transitions",
    "publishers",
    "evidence",
    "metrics"
  ]
}
```

---

### Ingest Observation

```
POST /v1/observations
```

Ingest a new observation from any source. Triggers candidate extraction and policy evaluation.

**Request Body:**

```json
{
  "tenant_id": "acme-corp",
  "source": "email",
  "text": "Køb nye kontormøbler til kontoret",
  "external_id": "msg-12345",
  "actor": "john.doe@acme.com",
  "occurred_at": "2026-09-05T10:30:00Z",
  "metadata": {
    "priority": "high",
    "department": "operations"
  },
  "title_hint": "Kontormøbler",
  "owner_hint": "john.doe@acme.com",
  "due_hint": "2026-09-10",
  "priority_hint": "high"
}
```

**Required Fields:**
- `tenant_id`: Tenant identifier (1-128 chars)
- `source`: Source system (1-64 chars)
- `text`: Observation text (1-100,000 chars)

**Optional Fields:**
- `external_id`: External reference ID
- `actor`: Person who generated the observation
- `occurred_at`: When the observation occurred
- `metadata`: Additional key-value pairs
- `title_hint`: Suggested title for the work item
- `owner_hint`: Suggested assignee
- `due_hint`: Suggested due date
- `priority_hint`: Priority level (low|medium|high|critical)

**Response (201 Created):**

```json
{
  "action": "created",
  "work_item_id": "wi_abc123",
  "candidate_count": 2
}
```

**Response (202 Accepted):**

```json
{
  "action": "observed",
  "work_item_id": "wi_abc123",
  "candidate_count": 0
}
```

---

### List Work Items

```
GET /v1/work-items?tenant_id=acme-corp&limit=100
```

List work items for a tenant.

**Query Parameters:**
- `tenant_id` (required): Tenant identifier
- `limit` (optional): Max items to return (1-1000, default 100)

**Response:**

```json
{
  "count": 2,
  "work_items": [
    {
      "id": "wi_abc123",
      "tenant_id": "acme-corp",
      "title": "Køb kontormøbler",
      "status": "candidate",
      "canonical_key": "abc123def456",
      "created_at": "2026-09-05T10:30:00Z",
      "updated_at": "2026-09-05T10:30:00Z"
    }
  ]
}
```

---

### Get Work Item

```
GET /v1/work-items/{work_item_id}?tenant_id=acme-corp
```

Get detailed information about a specific work item.

**Response:**

```json
{
  "work_item": {
    "id": "wi_abc123",
    "tenant_id": "acme-corp",
    "title": "Køb kontormøbler",
    "status": "candidate",
    "canonical_key": "abc123def456",
    "created_at": "2026-09-05T10:30:00Z",
    "updated_at": "2026-09-05T10:30:00Z"
  },
  "observations": [
    {
      "id": "obs_xyz789",
      "source": "email",
      "external_id": "msg-12345",
      "actor": "john.doe@acme.com",
      "occurred_at": "2026-09-05T10:30:00Z",
      "text": "Køb nye kontormøbler til kontoret"
    }
  ],
  "transitions": [],
  "publications": []
}
```

---

### Review Work Item

```
POST /v1/work-items/{work_item_id}/review?tenant_id=acme-corp
```

Review a work item (approve, reject, snooze, or cancel).

**Request Body:**

```json
{
  "action": "approve",
  "actor": "manager@acme.com",
  "reason": "Approved for Q4 budget",
  "resume_at": null
}
```

**Actions:**
- `approve`: Move to APPROVED state
- `reject`: Move to REJECTED state
- `snooze`: Move to SNOOZED state (requires `resume_at`)
- `cancel`: Move to CANCELLED state

**Response:**

```json
{
  "id": "wi_abc123",
  "status": "approved",
  "updated_at": "2026-09-05T11:00:00Z"
}
```

---

### Promote to WORKS

```
POST /v1/work-items/{work_item_id}/promote?tenant_id=acme-corp
```

Promote an approved work item to WORKS execution. Requires `APPROVED` status.

**Request Body:**

```json
{
  "actor": "manager@acme.com",
  "reason": "Ready for execution"
}
```

**Response:**

```json
{
  "id": "wi_abc123",
  "status": "promoted",
  "updated_at": "2026-09-05T11:05:00Z"
}
```

**Error (403 Forbidden):**

```json
{
  "detail": "requires status APPROVED"
}
```

---

### Publish Work Item

```
POST /v1/work-items/{work_item_id}/publish?tenant_id=acme-corp
```

Publish a work item to an external destination (RenOS, WORKS, etc.).

**Request Body:**

```json
{
  "destination": "renos-jobs"
}
```

**Response (201 Created):**

```json
{
  "id": "pub_abc123",
  "work_item_id": "wi_abc123",
  "destination": "renos-jobs",
  "external_id": "job-456",
  "response": {
    "status": "queued"
  },
  "published_at": "2026-09-05T11:10:00Z"
}
```

---

### Get Evidence

```
GET /v1/work-items/{work_item_id}/evidence?tenant_id=acme-corp
```

Get HMAC-signed evidence envelope for a work item.

**Response:**

```json
{
  "payload": {
    "tenant_id": "acme-corp",
    "work_item_id": "wi_abc123",
    "title": "Køb kontormøbler",
    "canonical_key": "abc123def456",
    "observations": [...]
  },
  "hmac": "sha256:abc123...",
  "algorithm": "HMAC-SHA256",
  "created_at": "2026-09-05T11:15:00Z"
}
```

---

### Get Metrics

```
GET /v1/metrics
```

Get service metrics (ingest count, rejection count, etc.).

**Response:**

```json
{
  "total_ingested": 150,
  "total_candidates": 45,
  "total_approved": 12,
  "total_published": 8,
  "total_rejected": 3,
  "total_cancelled": 2,
  "total_snoozed": 5,
  "total_promoted": 6,
  "by_status": {
    "candidate": 30,
    "approved": 12,
    "published": 8,
    "rejected": 3,
    "cancelled": 2,
    "snoozed": 5,
    "promoted": 6
  },
  "by_source": {
    "email": 50,
    "conversation": 40,
    "calendar": 30,
    "code": 20,
    "renos": 10
  }
}
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AFTERGRAPH_API_TOKEN` | None | Bearer token for authentication |
| `AFTERGRAPH_DB` | `./aftergraph-work-intelligence.db` | SQLite database path |
| `AFTERGRAPH_HOST` | `127.0.0.1` | Server host |
| `AFTERGRAPH_PORT` | `8087` | Server port |
| `AFTERGRAPH_RATE_LIMIT` | `60` | Requests per minute per IP |
| `AFTERGRAPH_EVIDENCE_SECRET` | `aftergraph-work-intelligence` | HMAC signing secret |
| `AFTERGRAPH_PUBLISHER` | None | Publisher destination (renos, works) |
| `RENOS_SESSION_TOKEN` | None | RenOS session token for live integration |

---

## Error Codes

| Code | Description |
|------|-------------|
| 400 | Bad request (invalid payload, missing fields) |
| 401 | Unauthorized (missing or invalid bearer token) |
| 403 | Forbidden (e.g., promoting non-APPROVED item) |
| 404 | Not found (work item doesn't exist) |
| 429 | Rate limit exceeded |
| 500 | Internal server error |
| 502 | Bad gateway (destination unreachable) |
| 503 | Service unavailable (no publisher configured) |
