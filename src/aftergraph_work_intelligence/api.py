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
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
import uuid

from .evidence import build_evidence
from .metrics import MetricsRecorder
from .models import ObservationInput, Publication, utc_now
from .policy import PolicyStore
from .publishers import Publisher, publisher_from_env
from .service import WorkIntelligenceService
from .store import SQLiteStore
from .transitions import TransitionEngine


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
    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
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
        return {"status": "ok", "service": "aftergraph-work-intelligence", "version": "0.2.0"}

    @router.post("/observations", dependencies=[Depends(auth)])
    def ingest_observation(payload: ObservationRequest, svc: WorkIntelligenceService = Depends(service)):
        try:
            result = svc.ingest(ObservationInput(**payload.model_dump()))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        status = 201 if result.action == "created" else 202 if result.action == "observed" else 200
        return JSONResponse(status_code=status, content=jsonable_encoder(asdict(result)))

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
        return jsonable_encoder(asdict(item))

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
        return jsonable_encoder(asdict(item))

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