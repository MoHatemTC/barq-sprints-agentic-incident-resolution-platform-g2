"""Knowledge Base endpoints for ui/kb.html.

ServiceNow stays the source of truth: new articles are written to
kb_knowledge first, and the "Update Vector DB" button (POST
/api/v1/dashboard/kb-sync) copies them into Qdrant afterwards.

Heavy imports (Qdrant, embeddings, ServiceNow auth) are done inside the
functions so importing this router never needs the .env to be complete.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import PurePath
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/kb", tags=["kb"])

# Keep these in sync with data/kb_category_mapping.json and with the
# security levels your retrieval filters understand.
Category = Literal["software", "network", "hardware", "identity"]
SecurityLevel = Literal["public", "internal", "confidential"]
CATEGORIES = ["software", "network", "hardware", "identity"]
SECURITY_LEVELS = ["public", "internal", "confidential"]

MIN_BODY, MAX_BODY = 30, 50_000
MAX_UPLOAD_BYTES = 200_000
ALLOWED_EXTENSIONS = {".txt", ".md"}

_SERVICE_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_create_lock = threading.Lock()


@router.get("/articles")
def list_articles():
    """Published articles from ServiceNow, each tagged with its Qdrant status."""
    from qdrant_client import QdrantClient

    from src.config import QDRANT
    from src.retrieval.ingest import _content_hash, _ensure_collection, _stored_hashes
    from src.retrieval.sources.servicenow_source import load_articles_from_servicenow

    source_available = True
    source_error = None
    try:
        articles = load_articles_from_servicenow()
    except Exception as exc:
        logger.warning("ServiceNow unavailable while listing KB articles: %s", exc)
        source_available = False
        source_error = str(exc)
        articles = []

    stored: dict[str, str] | None
    try:
        client = QdrantClient(url=QDRANT.url, check_compatibility=False)
        _ensure_collection(client)
        stored = _stored_hashes(client)
    except Exception as exc:
        logger.warning("Qdrant unavailable while listing KB articles: %s", exc)
        stored = None

    rows = []
    summary = {"indexed": 0, "pending_sync": 0, "not_indexed": 0, "unknown": 0}
    for a in sorted(articles, key=lambda x: x.number):
        if stored is None:
            status = "unknown"
        elif a.article_id not in stored:
            status = "not_indexed"
        elif stored[a.article_id] != _content_hash(a):
            status = "pending_sync"
        else:
            status = "indexed"
        summary[status] += 1
        rows.append(
            {
                "sys_id": a.sys_id,
                "number": a.number,
                "title": a.title,
                "category": a.category,
                "service": a.service,
                "security_level": a.security_level,
                "version": a.version,
                "workflow_state": a.workflow_state,
                "preview": (a.body or "")[:400],
                "index_status": status,
            }
        )

    to_delete = 0
    if stored is not None:
        to_delete = len(set(stored) - {a.article_id for a in articles})

    return {
        "articles": rows,
        "count": len(rows),
        "summary": {**summary, "to_delete": to_delete},
        "index_available": stored is not None,
        "source_available": source_available,
        "source_error": source_error,
        "options": {"categories": CATEGORIES, "security_levels": SECURITY_LEVELS},
    }


class NewArticle(BaseModel):
    title: str = Field(min_length=5, max_length=160)
    body: str = Field(min_length=MIN_BODY, max_length=MAX_BODY)
    category: Category
    service: str = Field(default="general", min_length=2, max_length=60)
    security_level: SecurityLevel = "internal"

    @field_validator("title", "body", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("service", mode="before")
    @classmethod
    def _normalize_service(cls, v):
        return re.sub(r"\s+", "-", v.strip().lower()) if isinstance(v, str) else v

    @field_validator("service")
    @classmethod
    def _check_service(cls, v):
        if not _SERVICE_RE.match(v):
            raise ValueError("use lowercase letters, numbers, '-' or '_' (e.g. video-conferencing)")
        return v


class _NotPublished(Exception):
    def __init__(self, sys_id: str, reason: str):
        super().__init__(reason)
        self.sys_id = sys_id
        self.reason = reason


def _next_article_number(existing: list[str]) -> str:
    """Next KB00NN after the highest 4-digit number. ServiceNow's own
    7-digit numbers (KB0010222...) are ignored on purpose."""
    nums = []
    for n in existing:
        m = re.fullmatch(r"KB(\d{4})", n or "")
        if m:
            nums.append(int(m.group(1)))
    return f"KB{max(nums, default=0) + 1:04d}"


def _publish_one(article) -> str:
    """POST one article to kb_knowledge, verify it, record the mapping."""
    import httpx

    from src.config import SERVICENOW
    from src.retrieval.publish_kb import (
        _build_payload,
        _verify_or_publish,
        load_mapping,
        save_mapping,
    )
    from src.retrieval.servicenow_auth import ServiceNowOAuthClient
    from src.servicenow.exceptions import ServiceNowWriteNotAppliedError

    auth = ServiceNowOAuthClient()
    payload = _build_payload(article)

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{SERVICENOW.instance_url}/api/now/table/{SERVICENOW.kb_table}",
            headers={**auth.auth_headers(), "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        sys_id = resp.json()["result"]["sys_id"]

        # Record the mapping right away so a retry (or publish_kb) updates this
        # record instead of creating a duplicate.
        mapping = load_mapping()
        mapping[article.article_id] = sys_id
        save_mapping(mapping)

        try:
            _verify_or_publish(payload, sys_id, client, auth)
        except ServiceNowWriteNotAppliedError as exc:
            raise _NotPublished(sys_id, str(exc)) from exc
    return sys_id


@router.post("/articles")
def create_article(payload: NewArticle):
    import httpx

    from src.retrieval.publish_kb import load_mapping
    from src.retrieval.schema import Article
    from src.retrieval.servicenow_auth import ServiceNowAuthError
    from src.retrieval.sources.servicenow_source import load_articles_from_servicenow

    with _create_lock:  # two people saving at once must not get the same number
        try:
            existing = [a.number for a in load_articles_from_servicenow()]
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"Could not read articles from ServiceNow: {exc}"
            ) from exc

        number = _next_article_number(existing + list(load_mapping().keys()))
        article = Article(
            sys_id="",
            number=number,
            article_id=number,
            title=payload.title,
            body=payload.body,
            category=payload.category,
            service=payload.service,
            workflow_state="published",
            version=1,
            security_level=payload.security_level,
        )

        try:
            sys_id = _publish_one(article)
        except _NotPublished as exc:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"{number} was created in ServiceNow (sys_id={exc.sys_id}) but is not "
                    f"published yet: {exc.reason}. Publish it in ServiceNow, or run "
                    f"'python -m src.retrieval.publish_kb' to retry."
                ),
            ) from exc
        except ServiceNowAuthError as exc:
            raise HTTPException(status_code=502, detail=f"ServiceNow login failed: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"ServiceNow rejected the article (HTTP {exc.response.status_code}).",
            ) from exc
        except Exception as exc:
            logger.exception("KB article create failed")
            raise HTTPException(status_code=500, detail=f"Could not save article: {exc}") from exc

    return {
        "status": "created",
        "number": number,
        "sys_id": sys_id,
        "next_step": "Run Update Vector DB so retrieval can find it.",
    }


class UploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content: str


@router.post("/articles/upload")
def parse_upload(req: UploadRequest):
    ext = PurePath(req.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail="Only .txt and .md files are supported.")
    if "\x00" in req.content:
        raise HTTPException(status_code=422, detail="This file is not plain text.")
    if len(req.content.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413, detail=f"File is too large (max {MAX_UPLOAD_BYTES // 1000} KB)."
        )

    text = req.content.replace("\r\n", "\n").strip()
    if len(text) < MIN_BODY:
        raise HTTPException(
            status_code=422, detail=f"File has too little text (at least {MIN_BODY} characters)."
        )

    lines = text.split("\n")
    first = lines[0].strip()
    if first.startswith("# "):
        title, body, source = first[2:].strip(), "\n".join(lines[1:]).strip(), "heading"
    else:
        stem = re.sub(r"[_\-]+", " ", PurePath(req.filename).stem).strip()
        title, body, source = (stem[:1].upper() + stem[1:]), text, "filename"

    return {
        "filename": req.filename,
        "title": title[:160],
        "body": body[:MAX_BODY],
        "title_source": source,
    }
