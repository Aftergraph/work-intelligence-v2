from __future__ import annotations

import argparse
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .models import ObservationInput, Publication, utc_now
from .publishers import Publisher, publisher_from_env
from .service import WorkIntelligenceService
from .store import SQLiteStore


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


def create_app(
    db_path: str | Path = "./aftergraph-work-intelligence.db",
    api_token: str | None = None,
    publisher: Publisher | None = None,
) -> FastAPI:
    db_path = Path(db_path)
    configured_token = api_token if api_token is not None else os.getenv("AFTERGRAPH_API_TOKEN")
    configured_publisher = publisher if publisher is not None else publisher_from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        store = SQLiteStore(db_path)
        app.state.store = store
        app.state.service = WorkIntelligenceService(store)
        app.state.publisher = configured_publisher
        try:
            yield
        finally:
            store.close()

    app = FastAPI(
        title="Aftergraph Work Intelligence",
        version="0.1.0",
        description="Source-neutral observation → WorkItem inference, resolution, provenance, and publication.",
        lifespan=lifespan,
    )
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
        return {"status": "ok", "service": "aftergraph-work-intelligence", "version": "0.1.0"}

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

    app.include_router(router)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Aftergraph Work Intelligence V1")
    parser.add_argument("--host", default=os.getenv("AFTERGRAPH_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AFTERGRAPH_PORT", "8087")))
    parser.add_argument("--db", default=os.getenv("AFTERGRAPH_DB", "./aftergraph-work-intelligence.db"))
    args = parser.parse_args()
    uvicorn.run(create_app(db_path=args.db), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
