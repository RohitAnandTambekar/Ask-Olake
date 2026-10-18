"""HTTP API.

POST /api/chat       Server-Sent Events stream (see answer.py for the event types); the widget
POST /ask            one JSON response, for scripts and integrations: {question} -> {answer, sources}
POST /api/feedback   thumbs up/down on an answer
GET  /api/model      which model generates answers: {provider, model, embedding_model}
GET  /api/health     readiness: database, active index, model match, LLM mode
GET  /               demo page with the widget
GET  /widget.js      the embeddable widget
"""

import json
import logging
import threading
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from olake_api import store
from olake_api.answer import Answerer
from olake_api.config import STATIC_DIR, get_settings
from olake_api.db import get_pool
from olake_api.guard import RateLimiter, client_hash
from olake_api.llm import make_llm
from olake_api.retrieval import IndexUnavailable
from olake_shared.embeddings import make_embedder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("olake_api")


@lru_cache
def get_answerer() -> Answerer:
    s = get_settings()
    return Answerer(
        settings=s,
        pool=get_pool(),
        embedder=make_embedder(
            s.embedding_provider, s.embedding_model, s.embedding_dims, s.openai_api_key
        ),
        llm=make_llm(s),
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    answerer = get_answerer()
    try:
        answerer.check_ready()
        log.info(
            "ready: llm=%s embedder=%s",
            answerer.llm.name if answerer.llm else "extractive",
            answerer.embedder.model,
        )
    except IndexUnavailable as exc:
        log.error("NOT READY: %s", exc)  # keep serving /api/health so the problem is visible
    yield
    get_pool().close()


settings = get_settings()
app = FastAPI(
    title="Ask OLake", version="0.1.0", lifespan=lifespan, docs_url="/api/docs", redoc_url=None
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["content-type"],
    allow_credentials=False,
)
limiter = RateLimiter(settings.rate_limit_per_minute, settings.rate_limit_burst)
answer_slots = threading.BoundedSemaphore(settings.max_concurrent_answers)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


def _client_ip(request: Request) -> str:
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[Turn] = Field(default_factory=list, max_length=20)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class AskResponse(BaseModel):
    answer: str
    sources: list[str]  # URLs the answer cites, in first-use order; [] when it abstains


class FeedbackRequest(BaseModel):
    query_id: uuid.UUID
    rating: Literal[-1, 1]
    comment: str | None = Field(default=None, max_length=2000)


def _sse(events: Iterator[dict]) -> Iterator[str]:
    try:
        for e in events:
            yield f"event: {e['event']}\ndata: {json.dumps(e['data'], ensure_ascii=False)}\n\n"
    except Exception:
        log.exception("chat stream failed")
        payload = json.dumps({"message": "Something went wrong. Please try again."})
        yield f"event: error\ndata: {payload}\n\n"
    finally:
        answer_slots.release()


@app.post("/api/chat")
def chat(body: ChatRequest, request: Request) -> StreamingResponse:
    ip = _client_ip(request)
    if not limiter.allow(ip):
        raise HTTPException(status_code=429, detail="Too many questions. Please wait a minute.")
    if not answer_slots.acquire(blocking=False):
        raise HTTPException(
            status_code=503, detail="The assistant is busy. Please try again shortly."
        )
    answerer = get_answerer()
    events = answerer.run(
        body.message,
        [t.model_dump() for t in body.history],
        client=client_hash(ip, settings.client_hash_salt),
    )
    return StreamingResponse(
        _sse(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, request: Request) -> AskResponse:
    """Same pipeline as /api/chat, collected into a single response instead of streamed."""
    ip = _client_ip(request)
    if not limiter.allow(ip):
        raise HTTPException(status_code=429, detail="Too many questions. Please wait a minute.")
    if not answer_slots.acquire(blocking=False):
        raise HTTPException(
            status_code=503, detail="The assistant is busy. Please try again shortly."
        )
    try:
        sources: list[dict] = []
        final: dict | None = None
        events = get_answerer().run(
            body.question, [], client=client_hash(ip, settings.client_hash_salt)
        )
        for e in events:
            if e["event"] == "sources":
                sources = e["data"]["sources"]
            elif e["event"] == "final":
                final = e["data"]
            elif e["event"] == "error":
                raise HTTPException(status_code=503, detail=e["data"]["message"])
    finally:
        answer_slots.release()
    if final is None:
        raise HTTPException(status_code=503, detail="Something went wrong. Please try again.")
    cited = [sources[n - 1]["url"] for n in final["citations"] if 0 < n <= len(sources)]
    return AskResponse(answer=final["answer"], sources=list(dict.fromkeys(cited)))


@app.post("/api/feedback", status_code=204)
def feedback(body: FeedbackRequest, request: Request) -> Response:
    if not limiter.allow(f"fb:{_client_ip(request)}"):
        raise HTTPException(status_code=429, detail="Too many requests.")
    if not store.record_feedback(get_pool(), body.query_id, body.rating, body.comment):
        raise HTTPException(status_code=404, detail="Unknown query id.")
    return Response(status_code=204)


class ModelInfo(BaseModel):
    provider: str  # openrouter | anthropic | openai | extractive
    model: str | None  # None in extractive mode: answers are passages, not generated text
    embedding_model: str


@app.get("/api/model", response_model=ModelInfo)
def model_info() -> ModelInfo:
    """Which model writes the answers (and which one embeds the questions)."""
    answerer = get_answerer()
    llm = answerer.llm
    return ModelInfo(
        provider=llm.name.split(":", 1)[0] if llm else "extractive",
        model=llm.model if llm else None,
        embedding_model=answerer.embedder.model,
    )


@app.get("/api/health")
def health() -> JSONResponse:
    answerer = get_answerer()
    info: dict = {
        "llm": answerer.llm.name if answerer.llm else "extractive",
        "embedding_model": answerer.embedder.model,
    }
    try:
        answerer.check_ready()
        with get_pool().connection() as conn:
            row = conn.execute(
                "select id, embedding_model, manifest->>'chunks', created_at from index_versions where status = 'active'"
            ).fetchone()
        info.update(
            status="ok",
            index_version=row[0],
            chunks=int(row[2] or 0),
            index_built=row[3].isoformat(),
        )
        return JSONResponse(info)
    except Exception as exc:  # noqa: BLE001 - health must report, not raise
        info.update(status="unavailable", reason=str(exc))
        return JSONResponse(info, status_code=503)


@app.get("/widget.js", include_in_schema=False)
def widget() -> FileResponse:
    path = STATIC_DIR / "widget.js"
    if not path.exists():
        raise HTTPException(
            status_code=404, detail="widget not built: run `npm run build` in apps/widget"
        )
    return FileResponse(
        path, media_type="text/javascript", headers={"Cache-Control": "public, max-age=300"}
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")
