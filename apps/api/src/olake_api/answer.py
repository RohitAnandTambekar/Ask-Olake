"""Answer one question: scrub → analyse → retrieve → gate → generate → verify → log.

Yields events for the client (sent as Server-Sent Events by main.py):
  meta     {query_id, notices}           first, always
  sources  {sources: [...]}              the numbered sources given to the model
  delta    {text}                        streamed answer text (may be revised by `final`)
  final    {status, answer, citations, warnings}
  error    {message}

`final.answer` is the verified text; clients should replace the streamed text with it.
status: answered | partial (answered with warnings or extractive) | abstained | error
"""

import logging
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from olake_api import store
from olake_api.config import Settings
from olake_api.guard import scrub
from olake_api.llm import LLM, LLMError
from olake_api.prompts import NOT_FOUND, build_messages
from olake_api.query import analyze
from olake_api.retrieval import (
    Hit,
    IndexUnavailable,
    RetrievalResult,
    active_index,
    index_model,
    search,
)
from olake_api.verify import verify
from olake_shared.embeddings import Embedder, EmbeddingError

log = logging.getLogger("olake_api.answer")

SLACK_URL = "https://olake.io/slack/"
ISSUES_URL = "https://github.com/datazip-inc/olake/issues"
DETECT_CHARS = len(NOT_FOUND) + 2
RELATED_COVERAGE = 0.34  # share of question keywords the suggested pages must contain  # buffer this much before streaming, to catch NOT_FOUND


@dataclass
class Answerer:
    settings: Settings
    pool: object  # psycopg_pool.ConnectionPool
    embedder: Embedder
    llm: LLM | None  # None = extractive mode
    index_version: int | None = None  # None = the active version; set by evaluation

    def check_ready(self) -> None:
        """Raise if the index is missing or was built with a different embedding model."""
        with self.pool.connection() as conn:
            if self.index_version is None:
                _version, model = active_index(conn)
            else:
                model = index_model(conn, self.index_version)
        if model != self.embedder.model:
            raise IndexUnavailable(
                f"active index was built with {model!r} but the API embeds with {self.embedder.model!r}"
            )

    # ---------- helpers ----------

    @staticmethod
    def _source_payload(hits: list[Hit]) -> list[dict]:
        return [
            {
                "n": n,
                "title": h.title,
                "url": h.url,
                "breadcrumb": h.breadcrumb,
                "source_type": h.source_type,
                "product": h.product,
                "trust_tier": h.trust_tier,
            }
            for n, h in enumerate(hits, start=1)
        ]

    @staticmethod
    def _abstain_text(hits: list[Hit], reason: str | None = None) -> str:
        lines = [
            reason or "I couldn't find an answer to that in the OLake documentation or source code."
        ]
        if hits:
            lines.append("\nThese pages are the closest matches, in case they help:")
            seen: set[str] = set()
            for h in hits[:3]:
                if h.url not in seen:
                    seen.add(h.url)
                    lines.append(f"- [{h.title}]({h.url})")
        lines.append(
            f"\nYou can also ask the OLake community on [Slack]({SLACK_URL}) "
            f"or open an issue on [GitHub]({ISSUES_URL})."
        )
        return "\n".join(lines)

    @staticmethod
    def _extractive_text(hits: list[Hit]) -> str:
        parts = ["I can't write a summary right now, but these passages answer the closest match:"]
        for n, h in enumerate(hits[:3], start=1):
            excerpt = h.display_text.strip()
            if len(excerpt) > 700:
                excerpt = excerpt[:700].rsplit(" ", 1)[0] + " …"
                if excerpt.count("```") % 2:  # cut inside a code block: close it
                    excerpt += "\n```"
            parts.append(f"\n**{h.title}** [{n}]\n\n{excerpt}")
        return "\n".join(parts)

    # ---------- main flow ----------

    def run(self, question: str, history: list[dict], client: str | None = None) -> Iterator[dict]:
        started = time.monotonic()
        query_id = uuid.uuid4()
        first_token_ms: int | None = None
        result: RetrievalResult | None = None
        final: dict = {"status": "error", "answer": None, "citations": [], "warnings": []}
        model_name = self.llm.name if self.llm else "extractive"

        cleaned_q = scrub(question)
        cleaned_history = []
        found = list(cleaned_q.found)
        for m in history[-self.settings.max_history_messages :]:
            s = scrub(m.get("content", "")[:8000])
            found += s.found
            cleaned_history.append({"role": m.get("role"), "content": s.text})
        notices = []
        if found:
            notices.append(
                "Your message contained what looked like credentials ("
                + ", ".join(dict.fromkeys(found))
                + "). They were removed before processing. Please rotate them if they are real."
            )
        q = cleaned_q.text[: self.settings.max_question_chars]
        yield {"event": "meta", "data": {"query_id": str(query_id), "notices": notices}}

        try:
            analysis = analyze(q, cleaned_history)
            vector = self.embedder.embed([analysis.search_text])[0]
            with self.pool.connection() as conn:
                result = search(
                    conn,
                    analysis,
                    vector,
                    top_k=self.settings.top_k,
                    candidates=self.settings.candidates,
                    version=self.index_version,
                )
            hits = result.hits
            yield {"event": "sources", "data": {"sources": self._source_payload(hits)}}

            if not result.answerable(self.settings.similarity_floor):
                # off-topic questions get no "closest matches": they would only be noise
                related = hits if result.coverage >= RELATED_COVERAGE else []
                text = self._abstain_text(related)
                final = {"status": "abstained", "answer": text, "citations": [], "warnings": []}
            elif self.llm is None:
                final = {
                    "status": "partial",
                    "answer": self._extractive_text(hits),
                    "citations": [1, 2, 3][: len(hits)],
                    "warnings": [
                        "Written answers are unavailable; showing the most relevant passages."
                    ],
                }
            else:
                buffer, streamed, refusing, full = "", False, False, []
                try:
                    for delta in self.llm.stream(build_messages(q, cleaned_history, hits)):
                        if first_token_ms is None:
                            first_token_ms = int((time.monotonic() - started) * 1000)
                        full.append(delta)
                        if streamed:
                            yield {"event": "delta", "data": {"text": delta}}
                            continue
                        if refusing:
                            continue  # read the reason silently; it goes into `final`
                        buffer += delta
                        if len(buffer) >= DETECT_CHARS:
                            if buffer.lstrip().startswith(NOT_FOUND):
                                refusing = True  # never stream a refusal token by token
                                continue
                            streamed = True
                            yield {"event": "delta", "data": {"text": buffer}}
                except LLMError as exc:
                    log.warning("LLM failed: %s", exc)
                    if streamed:
                        raise
                    final = {
                        "status": "partial",
                        "answer": self._extractive_text(hits),
                        "citations": [1, 2, 3][: len(hits)],
                        "warnings": [
                            "The answer service is temporarily unavailable; showing the most relevant passages."
                        ],
                    }
                else:
                    text = "".join(full).strip()
                    if text.startswith(NOT_FOUND):
                        reason = text[len(NOT_FOUND) :].strip(" :\n") or None
                        missing = (
                            f"I couldn't find that in the OLake docs or code. {reason}"
                            if reason
                            else None
                        )
                        final = {
                            "status": "abstained",
                            "answer": self._abstain_text(hits, missing),
                            "citations": [],
                            "warnings": [],
                        }
                    else:
                        checked = verify(text, [h.text for h in hits], [h.url for h in hits])
                        if checked.status == "ungrounded":
                            final = {
                                "status": "abstained",
                                "answer": self._abstain_text(
                                    hits,
                                    "I couldn't produce an answer that is backed by the OLake docs or code.",
                                ),
                                "citations": [],
                                "warnings": [],
                            }
                        else:
                            final = {
                                "status": checked.status,
                                "answer": checked.text,
                                "citations": checked.citations,
                                "warnings": checked.warnings,
                            }
        except (IndexUnavailable, EmbeddingError) as exc:
            log.error("cannot answer: %s", exc)
            final = {"status": "error", "answer": None, "citations": [], "warnings": []}
            yield {
                "event": "error",
                "data": {
                    "message": "The assistant is temporarily unavailable. Please try again soon."
                },
            }
        except LLMError as exc:
            log.error("LLM failed mid-answer: %s", exc)
            final = {"status": "error", "answer": None, "citations": [], "warnings": []}
            yield {
                "event": "error",
                "data": {"message": "The answer was interrupted. Please try again."},
            }

        if final["status"] != "error":
            yield {"event": "final", "data": final}

        hits = result.hits if result else []
        store.log_query(
            self.pool,
            query_id=query_id,
            index_version=result.index_version if result else None,
            question=q,
            answer=final.get("answer"),
            status=final["status"],
            cited_urls=[hits[n - 1].url for n in final.get("citations", []) if 0 < n <= len(hits)],
            retrieved=[
                {
                    "id": h.id,
                    "url": h.url,
                    "score": round(h.score, 5),
                    "similarity": round(h.similarity or 0, 4),
                    "matched_by": h.matched_by,
                }
                for h in hits
            ],
            model=model_name,
            latency_ms=int((time.monotonic() - started) * 1000),
            first_token_ms=first_token_ms,
            client_hash=client,
        )
