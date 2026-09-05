# Aftergraph Placement and Integration

## Canonical placement

Based on the current Aftergraph governance boundaries:

- **Research paper:** `Aftergraph/intelligence-systems-research/PAPERS/05-AUTONOMOUS-WORK-DETECTION.md`
- **Product V1:** recommended new repository `Aftergraph/work-intelligence`
- **Execution integration:** adapter to `Aftergraph/works-execution`, never direct inheritance of execution authority
- **Runtime enforcement:** consequential publication/execution remains subject to the existing Trust Gateway/AIE boundaries

The product should not be hidden inside RenOS and should not be hard-coded into WORKS. RenOS is the first tenant/dogfood consumer.

## Proposed contracts

### `observation/1.0`

```json
{
  "tenant_id": "renos",
  "source": "gmail|calendar|conversation|renos|codebase|api",
  "external_id": "source-specific-id",
  "actor": "optional",
  "text": "raw source evidence",
  "occurred_at": "RFC3339",
  "metadata": {}
}
```

### `aftergraph.work-item/1.0`

Canonical WorkItem plus supporting Observations. This is the payload emitted by the configured publisher adapter.

## Adapter rule

A connector converts source-native events to Observations. It does not contain domain-resolution logic.

A publisher converts a canonical WorkItem to a destination representation. It does not decide whether work exists.

This keeps Gmail, Calendar, RenOS, GitHub, and future ChatGPT/voice surfaces replaceable without changing the work-resolution core.
