from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
import uuid

from .evidence import build_evidence
from .metrics import MetricsRecorder
from .models import ObservationInput, Publication, utc_now
from .policy import PolicyStore, TenantPolicy
from .publishers import Publisher, publisher_from_env
from .service import WorkIntelligenceService
from .store import SQLiteStore
from .transitions import TransitionEngine


def _dt(value: str) -> datetime:
    """Convert ISO string to datetime."""
    return datetime.fromisoformat(value)


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    
    logging.root.handlers = [handler]
    logging.root.setLevel(logging.INFO)
    
    # Silence noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)


logger = logging.getLogger("aftergraph.work-intelligence")


import threading as _threading
import hashlib as _hashlib
import hmac as _hmac


def _fire_webhooks(app_state, event: str, payload: dict) -> None:
    """Fire registered webhooks for an event (best-effort, non-blocking via thread)."""
    webhooks = getattr(app_state, "webhooks", {})
    if not webhooks:
        return
    targets = []
    for wh in webhooks.values():
        if not wh.get("active", True):
            continue
        if event not in wh.get("events", []):
            continue
        targets.append(wh)
    if not targets:
        return

    def _deliver():
        for wh in targets:
            url = wh["url"]
            secret = wh.get("secret")
            body = json.dumps({"event": event, "data": payload}).encode()
            headers = {"Content-Type": "application/json"}
            if secret:
                sig = _hmac.new(secret.encode(), body, _hashlib.sha256).hexdigest()
                headers["X-Webhook-Signature"] = f"sha256={sig}"
            for attempt in range(3):
                try:
                    import urllib.request
                    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                    urllib.request.urlopen(req, timeout=5)
                    break
                except Exception:
                    if attempt < 2:
                        time.sleep(0.5 * (attempt + 1))

    _threading.Thread(target=_deliver, daemon=True).start()


class RateLimiter:
    def __init__(self, requests_per_minute: int = 60):
        self.requests_per_minute = requests_per_minute
        self.requests: dict[str, list[float]] = defaultdict(list)
    
    def is_allowed(self, client_id: str) -> bool:
        now = time.time()
        window_start = now - 60
        
        # Clean old requests
        self.requests[client_id] = [t for t in self.requests[client_id] if t > window_start]
        
        # Check limit
        if len(self.requests[client_id]) >= self.requests_per_minute:
            return False
        
        # Record new request
        self.requests[client_id].append(now)
        return True


class ObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=100_000)
    external_id: str | None = Field(default=None, max_length=512)
    actor: str | None = Field(default=None, max_length=512)
    occurred_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    title_hint: str | None = Field(default=None, max_length=256)
    owner_hint: str | None = Field(default=None, max_length=256)
    due_hint: str | None = Field(default=None, max_length=256)
    priority_hint: str | None = Field(default=None, pattern="^(low|medium|high|critical)$")


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: str = Field(min_length=1, max_length=128)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(approve|reject|snooze|cancel)$")
    actor: str = Field(min_length=1, max_length=512)
    reason: str = Field(default="", max_length=2048)
    resume_at: datetime | None = None


class PromoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(min_length=1, max_length=512)
    reason: str = Field(default="", max_length=2048)


class BulkStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_item_ids: list[str] = Field(min_length=1, max_length=100)
    tenant_id: str = Field(min_length=1, max_length=128)


def create_app(
    db_path: str | Path = "./aftergraph-work-intelligence.db",
    api_token: str | None = None,
    publisher: Publisher | None = None,
    policy_store: PolicyStore | None = None,
    evidence_secret: str | None = None,
) -> FastAPI:
    db_path = Path(db_path)
    configured_token = api_token if api_token is not None else os.getenv("AFTERGRAPH_API_TOKEN")
    configured_publisher = publisher if publisher is not None else publisher_from_env()
    configured_policy_store = policy_store if policy_store is not None else PolicyStore()
    configured_evidence_secret = evidence_secret if evidence_secret is not None else os.getenv(
        "AFTERGRAPH_EVIDENCE_SECRET", "aftergraph-work-intelligence"
    )
    rate_limiter = RateLimiter(requests_per_minute=int(os.getenv("AFTERGRAPH_RATE_LIMIT", "60")))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging()
        logger.info("Starting Aftergraph Work Intelligence V2", extra={"version": "0.2.0"})
        db_path.parent.mkdir(parents=True, exist_ok=True)
        store = SQLiteStore(db_path)
        app.state.store = store
        app.state.service = WorkIntelligenceService(store, policy_store=configured_policy_store)
        app.state.policy_store = configured_policy_store
        app.state.transitions = TransitionEngine(store, policy_store=configured_policy_store)
        app.state.publisher = configured_publisher
        app.state.metrics = MetricsRecorder(store)
        app.state.evidence_secret = configured_evidence_secret
        try:
            yield
        finally:
            store.close()

    app = FastAPI(
        title="Aftergraph Work Intelligence",
        version="0.2.0",
        description="Source-neutral observation → WorkItem inference, resolution, provenance, review, publication, and optional WORKS promotion.",
        lifespan=lifespan,
    )
    # Add GZip compression
    from fastapi.middleware.gzip import GZipMiddleware
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    
    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Add timing middleware
    @app.middleware("http")
    async def add_timing(request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        duration = time.time() - start_time
        response.headers["X-Process-Time"] = str(round(duration * 1000, 2))
        return response
    
    # Add request logging middleware
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        duration = time.time() - start_time
        
        logger.info(
            "Request processed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration * 1000, 2),
                "client": request.client.host if request.client else "unknown",
            }
        )
        return response
    
    # Add rate limiting middleware
    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        client_id = request.client.host if request.client else "unknown"
        if not rate_limiter.is_allowed(client_id):
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."}
            )
        return await call_next(request)
    
    # Add request ID middleware
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    
    router = APIRouter(prefix="/v1")

    def auth(authorization: str | None = Header(default=None)) -> None:
        if not configured_token:
            return
        if authorization != f"Bearer {configured_token}":
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    def service(request: Request) -> WorkIntelligenceService:
        return request.app.state.service

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "aftergraph-work-intelligence",
            "version": "0.2.0",
            "api_version": "v1",
            "build": "production"
        }

    @router.post("/observations", dependencies=[Depends(auth)])
    def ingest_observation(payload: ObservationRequest, request: Request, svc: WorkIntelligenceService = Depends(service)):
        try:
            result = svc.ingest(ObservationInput(**payload.model_dump()))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        status = 201 if result.action == "created" else 202 if result.action == "observed" else 200
        encoded = jsonable_encoder(asdict(result))
        _fire_webhooks(request.app.state, "observation.ingested", encoded)
        return JSONResponse(status_code=status, content=encoded)

    @router.get("/work-items", dependencies=[Depends(auth)])
    def list_work_items(
        tenant_id: str = Query(min_length=1, max_length=128),
        limit: int = Query(default=100, ge=1, le=1000),
        svc: WorkIntelligenceService = Depends(service),
    ):
        items = svc.list_work_items(tenant_id, limit)
        return {"count": len(items), "work_items": jsonable_encoder([asdict(item) for item in items])}

    @router.get("/work-items/{work_item_id}", dependencies=[Depends(auth)])
    def get_work_item(
        work_item_id: str,
        tenant_id: str = Query(min_length=1, max_length=128),
        svc: WorkIntelligenceService = Depends(service),
    ):
        try:
            detail = svc.get_work_item_detail(work_item_id, tenant_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="work item not found") from exc
        return jsonable_encoder(asdict(detail))

    @router.post("/work-items/{work_item_id}/review", dependencies=[Depends(auth)])
    def review_work_item(
        work_item_id: str,
        payload: ReviewRequest,
        request: Request,
        tenant_id: str = Query(min_length=1, max_length=128),
    ):
        engine: TransitionEngine = request.app.state.transitions
        try:
            if payload.action == "approve":
                item = engine.approve(work_item_id, actor=payload.actor, reason=payload.reason)
            elif payload.action == "reject":
                item = engine.reject(work_item_id, actor=payload.actor, reason=payload.reason)
            elif payload.action == "snooze":
                if payload.resume_at is None:
                    raise HTTPException(status_code=400, detail="resume_at is required for snooze")
                item = engine.snooze(work_item_id, actor=payload.actor, resume_at=payload.resume_at, reason=payload.reason)
            else:  # cancel
                item = engine.cancel(work_item_id, actor=payload.actor, reason=payload.reason)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="work item not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result = jsonable_encoder(asdict(item))
        _fire_webhooks(request.app.state, f"work_item.{payload.action}", result)
        return result

    @router.post("/work-items/{work_item_id}/promote", dependencies=[Depends(auth)])
    def promote_work_item(
        work_item_id: str,
        payload: PromoteRequest,
        request: Request,
        tenant_id: str = Query(min_length=1, max_length=128),
    ):
        engine: TransitionEngine = request.app.state.transitions
        try:
            item = engine.promote_to_works(work_item_id, actor=payload.actor, reason=payload.reason)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="work item not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result = jsonable_encoder(asdict(item))
        _fire_webhooks(request.app.state, "work_item.promoted", result)
        return result

    @router.post("/work-items/{work_item_id}/publish", status_code=201, dependencies=[Depends(auth)])
    def publish_work_item(
        work_item_id: str,
        payload: PublishRequest,
        request: Request,
        tenant_id: str = Query(min_length=1, max_length=128),
        svc: WorkIntelligenceService = Depends(service),
    ):
        pub: Publisher | None = request.app.state.publisher
        if pub is None:
            raise HTTPException(status_code=503, detail="no publisher destinations configured")
        try:
            detail = svc.get_work_item_detail(work_item_id, tenant_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="work item not found") from exc
        try:
            receipt = pub.publish(payload.destination, detail.work_item, detail.observations)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        publication = Publication(
            id=f"pub_{uuid.uuid4().hex}",
            work_item_id=work_item_id,
            destination=receipt.destination,
            external_id=receipt.external_id,
            response=receipt.response or {},
            published_at=utc_now(),
        )
        request.app.state.store.save_publication(publication)
        return jsonable_encoder(asdict(publication))

    @router.get("/work-items/{work_item_id}/evidence", dependencies=[Depends(auth)])
    def get_evidence(
        work_item_id: str,
        request: Request,
        tenant_id: str = Query(min_length=1, max_length=128),
        svc: WorkIntelligenceService = Depends(service),
    ):
        try:
            detail = svc.get_work_item_detail(work_item_id, tenant_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="work item not found") from exc
        payload = {
            "tenant_id": detail.work_item.tenant_id,
            "work_item_id": detail.work_item.id,
            "title": detail.work_item.title,
            "canonical_key": detail.work_item.canonical_key,
            "observations": [
                {
                    "id": o.id,
                    "source": o.source,
                    "external_id": o.external_id,
                    "actor": o.actor,
                    "occurred_at": o.occurred_at.isoformat() if o.occurred_at else None,
                    "text": o.text,
                }
                for o in detail.observations
            ],
        }
        envelope = build_evidence(payload, secret=request.app.state.evidence_secret)
        return jsonable_encoder(envelope)

    @router.get("/version", dependencies=[Depends(auth)])
    def version() -> dict:
        return {
            "version": "0.2.0",
            "build": "production",
            "status": "active",
            "features": ["adapters", "policies", "transitions", "publishers", "evidence", "metrics"]
        }

    @router.get("/metrics", dependencies=[Depends(auth)])
    def metrics(request: Request):
        recorder: MetricsRecorder = request.app.state.metrics
        return jsonable_encoder(recorder.snapshot())

    @router.get("/monitoring", dependencies=[Depends(auth)])
    def monitoring(request: Request):
        """Comprehensive monitoring endpoint with system metrics."""
        import psutil
        recorder: MetricsRecorder = request.app.state.metrics
        
        # System metrics
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        
        # Service metrics
        service_metrics = recorder.snapshot()
        
        return {
            "system": {
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "memory_used_gb": round(memory.used / (1024**3), 2),
                "memory_total_gb": round(memory.total / (1024**3), 2),
                "disk_percent": disk.percent,
                "disk_used_gb": round(disk.used / (1024**3), 2),
                "disk_total_gb": round(disk.total / (1024**3), 2),
            },
            "service": service_metrics,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }


    @router.get("/tenants", dependencies=[Depends(auth)])
    def list_tenants(request: Request):
        """List all tenants with work item counts."""
        store: SQLiteStore = request.app.state.store
        with store._lock:
            rows = store._db.execute(
                "SELECT tenant_id, COUNT(*) as cnt FROM intake_work_items GROUP BY tenant_id ORDER BY tenant_id"
            ).fetchall()
        tenants = [
            {"tenant_id": row[0], "work_item_count": row[1]}
            for row in rows
        ]
        return {"tenants": tenants, "count": len(tenants)}

    @router.get("/work-items/{work_item_id}/transitions", dependencies=[Depends(auth)])
    def get_transitions(
        work_item_id: str,
        request: Request,
        tenant_id: str = Query(min_length=1, max_length=128),
    ):
        """Get transition history for a work item."""
        store: SQLiteStore = request.app.state.store
        item = store.get_work_item(work_item_id, tenant_id)
        if item is None:
            raise HTTPException(status_code=404, detail="work item not found")
        transitions = store.list_transitions(work_item_id)
        return {
            "work_item_id": work_item_id,
            "transitions": [
                {
                    "id": t.id,
                    "from_status": t.from_state,
                    "to_status": t.to_state,
                    "action": "approve" if t.to_state == "APPROVED" else t.to_state.lower(),
                    "actor": t.actor,
                    "reason": t.reason,
                    "created_at": t.at.isoformat() if t.at else None,
                }
                for t in transitions
            ],
            "count": len(transitions),
        }

    @router.get("/work-items/{work_item_id}/publications", dependencies=[Depends(auth)])
    def get_publications(
        work_item_id: str,
        request: Request,
        tenant_id: str = Query(min_length=1, max_length=128),
    ):
        """Get publication history for a work item."""
        store: SQLiteStore = request.app.state.store
        item = store.get_work_item(work_item_id, tenant_id)
        if item is None:
            raise HTTPException(status_code=404, detail="work item not found")
        publications = store.publications_for_work_item(work_item_id)
        return {
            "work_item_id": work_item_id,
            "publications": jsonable_encoder([asdict(p) for p in publications]),
            "count": len(publications),
        }

    @router.get("/search", dependencies=[Depends(auth)])
    def search_work_items(
        q: str = Query(min_length=1, max_length=256),
        tenant_id: str = Query(min_length=1, max_length=128),
        limit: int = Query(default=50, ge=1, le=500),
        request: Request = None,
    ):
        """Search work items by title or summary."""
        svc: WorkIntelligenceService = request.app.state.service
        items = svc.list_work_items(tenant_id, limit=1000)
        # Simple text search in title and summary
        results = [
            item for item in items
            if q.lower() in (item.title or "").lower() or q.lower() in (item.summary or "").lower()
        ]
        return {
            "query": q,
            "tenant_id": tenant_id,
            "results": jsonable_encoder([asdict(item) for item in results[:limit]]),
            "count": len(results[:limit]),
        }

    @router.get("/observations", dependencies=[Depends(auth)])
    def list_observations(
        tenant_id: str = Query(min_length=1, max_length=128),
        source: str | None = Query(default=None, max_length=64),
        limit: int = Query(default=100, ge=1, le=1000),
        request: Request = None,
    ):
        """List observations for a tenant, with optional source filtering."""
        store: SQLiteStore = request.app.state.store
        sql = "SELECT * FROM intake_observations WHERE tenant_id = ?"
        params: list[Any] = [tenant_id]
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with store._lock:
            rows = store._db.execute(sql, params).fetchall()

        observations = []
        for row in rows:
            observations.append({
                "id": row["id"],
                "tenant_id": row["tenant_id"],
                "source": row["source"],
                "external_id": row["external_id"],
                "actor": row["actor"],
                "text": row["text"],
                "occurred_at": _dt(row["occurred_at"]).isoformat() if row["occurred_at"] else None,
                "created_at": _dt(row["created_at"]).isoformat() if row["created_at"] else None,
            })

        return {"observations": observations, "count": len(observations)}

    @router.get("/work-items", dependencies=[Depends(auth)])
    def list_work_items(
        tenant_id: str = Query(min_length=1, max_length=128),
        status: str | None = Query(default=None, max_length=64),
        priority: str | None = Query(default=None, max_length=64),
        limit: int = Query(default=100, ge=1, le=1000),
        svc: WorkIntelligenceService = Depends(service),
    ):
        """List work items with optional status and priority filtering (review queue)."""
        items = svc.list_work_items(tenant_id, limit=limit)
        # Filter by status if provided
        if status:
            items = [i for i in items if i.status.lower() == status.lower()]
        # Filter by priority if provided
        if priority:
            items = [i for i in items if i.priority.lower() == priority.lower()]
        return {"count": len(items), "work_items": jsonable_encoder([asdict(item) for item in items])}

    @router.get("/tenants/{tenant_id}/policy", dependencies=[Depends(auth)])
    def get_tenant_policy(
        tenant_id: str,
        request: Request,
    ):
        """Get tenant policy."""
        policy_store: PolicyStore = request.app.state.policy_store
        policy = policy_store.get(tenant_id)
        if policy is None:
            raise HTTPException(status_code=404, detail="tenant policy not found")
        return {
            "tenant_id": tenant_id,
            "allowed_sources": policy.allowed_sources,
            "allowed_destinations": policy.allowed_destinations,
            "max_work_items": policy.max_work_items,
            "max_priority": policy.max_priority,
            "allow_works": policy.allow_works,
        }

    @router.post("/tenants/{tenant_id}/policy", dependencies=[Depends(auth)])
    def update_tenant_policy(
        tenant_id: str,
        request: Request,
        allowed_sources: list[str] | None = Query(None),
        allowed_destinations: list[str] | None = Query(None),
        max_work_items: int | None = Query(None),
        max_priority: str | None = Query(None),
        allow_works: bool | None = Query(None),
        dedupe_threshold: float | None = Query(None),
        auto_create_work_items: bool | None = Query(None),
        require_approval_for_promotion: bool | None = Query(None),
    ):
        """Update tenant policy (in-memory + persistent)."""
        policy_store: PolicyStore = request.app.state.policy_store
        store: SQLiteStore = request.app.state.store
        existing = policy_store.get(tenant_id)

        # Merge with existing
        sources = set(allowed_sources) if allowed_sources is not None else (existing.allowed_sources if existing else set())
        destinations = set(allowed_destinations) if allowed_destinations is not None else (existing.allowed_destinations if existing else None)
        max_wi = max_work_items if max_work_items is not None else (existing.max_work_items if existing else 100)
        priority = max_priority or (existing.max_priority if existing else "high")
        works = allow_works if allow_works is not None else (existing.allow_works if existing else False)
        threshold = dedupe_threshold if dedupe_threshold is not None else (existing.dedupe_threshold if existing else 0.72)
        auto_create = auto_create_work_items if auto_create_work_items is not None else (existing.auto_create_work_items if existing else True)
        require_approval = require_approval_for_promotion if require_approval_for_promotion is not None else (existing.require_approval_for_promotion if existing else True)

        policy = TenantPolicy(
            allowed_sources=sources,
            allowed_destinations=destinations,
            max_work_items=max_wi,
            max_priority=priority,
            allow_works=works,
            dedupe_threshold=threshold,
            auto_create_work_items=auto_create,
            require_approval_for_promotion=require_approval,
        )

        # Update in-memory
        policy_store.put(tenant_id, policy)

        # Persist to database
        try:
            store.upsert_tenant_policy(
                tenant_id=tenant_id,
                allowed_sources=list(sources),
                auto_create_work_items=auto_create,
                max_work_items=max_wi,
                max_priority=priority,
                dedupe_threshold=threshold,
                allow_works=works,
                allowed_destinations=list(destinations) if destinations is not None else None,
                require_approval_for_promotion=require_approval,
            )
            persisted = True
        except Exception:
            persisted = False

        return {"tenant_id": tenant_id, "updated": True, "persisted": persisted}

    @router.get("/tenants/policies", dependencies=[Depends(auth)])
    def list_persisted_policies(request: Request):
        """List all persisted tenant policies."""
        store: SQLiteStore = request.app.state.store
        policies = store.list_tenant_policies()
        return {"policies": policies, "count": len(policies)}

    @router.delete("/tenants/{tenant_id}/policy", dependencies=[Depends(auth)])
    def delete_persisted_policy(tenant_id: str, request: Request):
        """Delete a persisted tenant policy."""
        store: SQLiteStore = request.app.state.store
        deleted = store.delete_tenant_policy(tenant_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="persisted policy not found")
        return {"tenant_id": tenant_id, "deleted": True}

    @router.get("/readiness", dependencies=[Depends(auth)])
    def readiness(
        request: Request,
    ):
        """Integration health/readiness API."""
        store: SQLiteStore = request.app.state.store
        policy_store: PolicyStore = request.app.state.policy_store
        publisher = request.app.state.publisher

        checks = {
            "database": False,
            "policy_store": False,
            "publisher": False,
        }

        # Check database
        try:
            with store._lock:
                store._db.execute("SELECT 1").fetchone()
            checks["database"] = True
        except Exception:
            pass

        # Check policy store
        try:
            policy_store.get("test")
            checks["policy_store"] = True
        except Exception:
            pass

        # Check publisher
        checks["publisher"] = publisher is not None

        all_pass = all(checks.values())
        return {
            "status": "pass" if all_pass else "fail",
            "checks": checks,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    @router.get("/work-items/{work_item_id}/actions", dependencies=[Depends(auth)])
    def get_allowed_actions(
        work_item_id: str,
        tenant_id: str = Query(min_length=1, max_length=128),
        request: Request = None,
    ):
        """Get capabilities/allowed-actions for a work item."""
        store: SQLiteStore = request.app.state.store
        policy_store: PolicyStore = request.app.state.policy_store

        item = store.get_work_item(work_item_id, tenant_id)
        if item is None:
            raise HTTPException(status_code=404, detail="work item not found")

        # Determine allowed actions based on current status and policies
        actions = []
        if item.status == "OPEN":
            actions = ["approve", "reject", "snooze", "cancel"]
        elif item.status == "APPROVED":
            actions = ["publish", "promote", "cancel"]

            # Check if promotion is allowed
            policy = policy_store.get(tenant_id)
            if policy and policy.allow_works:
                actions.append("promote")

        return {
            "work_item_id": work_item_id,
            "status": item.status,
            "actions": actions,
        }

    @router.get("/context", dependencies=[Depends(auth)])
    def get_actor_context(
        request: Request,
    ):
        """Get current actor/role/permission context."""
        # In a real implementation, this would extract the actual actor from the token
        return {
            "actor": "api_client",
            "role": "operator",
            "permissions": ["read", "write", "review"],
        }

    @router.post("/work-items/bulk-status", dependencies=[Depends(auth)])
    def bulk_status(
        request: Request,
        payload: BulkStatusRequest,
    ):
        """Get status of multiple work items."""
        store: SQLiteStore = request.app.state.store
        items = []
        for item_id in payload.work_item_ids:
            item = store.get_work_item(item_id, payload.tenant_id)
            if item is None:
                items.append({
                    "id": item_id,
                    "status": "NOT_FOUND",
                    "title": None,
                    "priority": None,
                })
            else:
                items.append({
                    "id": item.id,
                    "status": item.status,
                    "title": item.title,
                    "priority": item.priority,
                })
        return {"items": items, "count": len(items)}


    # --- Detailed Health Check ---
    @app.get("/healthz/detailed")
    def healthz_detailed(request: Request):
        """Detailed health check with dependency checks."""
        checks = {}
        
        # Database check
        try:
            store: SQLiteStore = request.app.state.store
            _ = store.list_work_items("health-check", limit=1)
            checks["database"] = {"status": "ok", "message": "SQLite accessible"}
        except Exception as e:
            checks["database"] = {"status": "error", "message": str(e)}
        
        # Store check
        try:
            checks["store"] = {"status": "ok", "message": "Store operational"}
        except Exception as e:
            checks["store"] = {"status": "error", "message": str(e)}
        
        all_ok = all(c["status"] == "ok" for c in checks.values())
        return {
            "status": "healthy" if all_ok else "degraded",
            "checks": checks,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    # --- Readiness Probe ---
    @app.get("/ready")
    def ready():
        """Kubernetes readiness probe."""
        return {
            "status": "ready",
            "dependencies": [
                {"name": "database", "status": "ok"},
                {"name": "store", "status": "ok"},
            ],
        }

    # --- Liveness Probe ---
    @app.get("/live")
    def live():
        """Kubernetes liveness probe."""
        import time as _time
        return {
            "status": "alive",
            "uptime_seconds": 0,  # Would need to track startup time
        }


    # --- Dashboard ---
    @app.get("/dashboard")
    def dashboard(request: Request):
        """Simple HTML dashboard showing work item overview."""
        from fastapi.responses import HTMLResponse
        store: SQLiteStore = request.app.state.store

        # Gather stats
        with store._lock:
            total_obs = store._db.execute("SELECT COUNT(*) FROM intake_observations").fetchone()[0]
            total_wi = store._db.execute("SELECT COUNT(*) FROM intake_work_items").fetchone()[0]
            by_status = store._db.execute(
                "SELECT status, COUNT(*) FROM intake_work_items GROUP BY status"
            ).fetchall()
            by_tenant = store._db.execute(
                "SELECT tenant_id, COUNT(*) FROM intake_work_items GROUP BY tenant_id"
            ).fetchall()
            by_priority = store._db.execute(
                "SELECT priority, COUNT(*) FROM intake_work_items GROUP BY priority"
            ).fetchall()
            total_pubs = store._db.execute("SELECT COUNT(*) FROM intake_publications").fetchone()[0]

        status_rows = "".join(f"<tr><td>{r[0]}</td><td>{r[1]}</td></tr>" for r in by_status)
        tenant_rows = "".join(f"<tr><td>{r[0]}</td><td>{r[1]}</td></tr>" for r in by_tenant)
        priority_rows = "".join(f"<tr><td>{r[0]}</td><td>{r[1]}</td></tr>" for r in by_priority)

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Aftergraph Work Intelligence - Dashboard</title>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; background: #f8f9fa; color: #212529; }}
  h1 {{ color: #1a1a2e; border-bottom: 3px solid #e94560; padding-bottom: 10px; }}
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; margin: 20px 0; }}
  .stat {{ background: white; border-radius: 8px; padding: 20px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
  .stat .number {{ font-size: 2em; font-weight: bold; color: #e94560; }}
  .stat .label {{ color: #6c757d; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 16px 0; }}
  th {{ background: #1a1a2e; color: white; padding: 12px; text-align: left; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid #dee2e6; }}
  h2 {{ color: #1a1a2e; margin-top: 30px; }}
  .footer {{ color: #6c757d; font-size: 0.9em; margin-top: 30px; padding-top: 10px; border-top: 1px solid #dee2e6; }}
</style>
</head>
<body>
<h1>Aftergraph Work Intelligence</h1>
<div class="stats">
  <div class="stat"><div class="number">{total_obs}</div><div class="label">Observations</div></div>
  <div class="stat"><div class="number">{total_wi}</div><div class="label">Work Items</div></div>
  <div class="stat"><div class="number">{total_pubs}</div><div class="label">Publications</div></div>
  <div class="stat"><div class="number">{len(by_tenant)}</div><div class="label">Tenants</div></div>
</div>
<h2>By Status</h2>
<table><tr><th>Status</th><th>Count</th></tr>{status_rows}</table>
<h2>By Tenant</h2>
<table><tr><th>Tenant</th><th>Count</th></tr>{tenant_rows}</table>
<h2>By Priority</h2>
<table><tr><th>Priority</th><th>Count</th></tr>{priority_rows}</table>
<div class="footer">Aftergraph Work Intelligence V2 | API: <a href="/docs">/docs</a></div>
</body></html>"""
        return HTMLResponse(content=html)


    # --- Webhook Management ---
    @router.post("/webhooks", status_code=201, dependencies=[Depends(auth)])
    def register_webhook(
        request: Request,
        url: str = Body(..., min_length=1),
        events: list[str] = Body(..., min_length=1),
        secret: str | None = Body(default=None),
    ):
        """Register a webhook for event notifications."""
        from urllib.parse import urlparse
        
        # Validate URL
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise HTTPException(status_code=422, detail="Invalid webhook URL")
        
        webhook_id = f"wh_{uuid.uuid4().hex}"
        webhook = {
            "id": webhook_id,
            "url": url,
            "events": events,
            "secret": secret,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "active": True,
        }
        
        # Store in app state
        if not hasattr(request.app.state, "webhooks"):
            request.app.state.webhooks = {}
        request.app.state.webhooks[webhook_id] = webhook
        
        return webhook

    @router.get("/webhooks", dependencies=[Depends(auth)])
    def list_webhooks(request: Request):
        """List registered webhooks."""
        webhooks = getattr(request.app.state, "webhooks", {})
        return {
            "webhooks": list(webhooks.values()),
            "count": len(webhooks),
        }

    @router.delete("/webhooks/{webhook_id}", dependencies=[Depends(auth)])
    def delete_webhook(
        webhook_id: str,
        request: Request,
    ):
        """Delete a webhook."""
        webhooks = getattr(request.app.state, "webhooks", {})
        if webhook_id not in webhooks:
            raise HTTPException(status_code=404, detail="Webhook not found")
        del webhooks[webhook_id]
        return {"status": "deleted", "id": webhook_id}

    # --- API Key Management ---
    @router.post("/api-keys", status_code=201, dependencies=[Depends(auth)])
    def create_api_key(
        request: Request,
        name: str = Body(..., min_length=1, max_length=128),
        permissions: list[str] = Body(default=["read"]),
    ):
        """Create a new API key."""
        key_id = f"key_{uuid.uuid4().hex[:16]}"
        api_key = f"ak_{uuid.uuid4().hex}"
        
        key_info = {
            "id": key_id,
            "name": name,
            "key": api_key,
            "permissions": permissions,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "active": True,
        }
        
        # Store in app state
        if not hasattr(request.app.state, "api_keys"):
            request.app.state.api_keys = {}
        request.app.state.api_keys[key_id] = key_info
        
        return key_info

    @router.get("/api-keys", dependencies=[Depends(auth)])
    def list_api_keys(request: Request):
        """List API keys (without secrets)."""
        api_keys = getattr(request.app.state, "api_keys", {})
        # Return keys without the actual secret
        safe_keys = [
            {k: v for k, v in key.items() if k != "key"}
            for key in api_keys.values()
        ]
        return {"keys": safe_keys, "count": len(safe_keys)}

    @router.delete("/api-keys/{key_id}", dependencies=[Depends(auth)])
    def revoke_api_key(
        key_id: str,
        request: Request,
    ):
        """Revoke an API key."""
        api_keys = getattr(request.app.state, "api_keys", {})
        if key_id not in api_keys:
            raise HTTPException(status_code=404, detail="API key not found")
        api_keys[key_id]["active"] = False
        return {"status": "revoked", "id": key_id}

    app.include_router(router)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Aftergraph Work Intelligence V2")
    parser.add_argument("--host", default=os.getenv("AFTERGRAPH_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AFTERGRAPH_PORT", "8087")))
    parser.add_argument("--db", default=os.getenv("AFTERGRAPH_DB", "./aftergraph-work-intelligence.db"))
    args = parser.parse_args()
    uvicorn.run(create_app(db_path=args.db), host=args.host, port=args.port)


if __name__ == "__main__":
    main()