# Ask OLake

A chat assistant for [OLake](https://olake.io/) that answers questions about OLake Go
(database, Kafka and S3 ingestion into Apache Iceberg) and OLake Fusion (Iceberg table
maintenance) from the official docs and the open-source code, cites every claim, and
says so when the answer isn't there.

![A written answer with citation pins and the source rail](docs/screenshots/answer.png)

- **Grounded:** the model only sees retrieved passages, must cite them, and every answer
  is checked: citations must point at real sources, config keys it mentions must exist in
  them, and an answer without citations is never shown.
- **Docs and code:** rendered docs pages (so tabs, shared snippets and React components are
  included) plus the Go source, Helm chart and Fusion docs. Pasting an error message
  finds the exact line of Go code that raises it.
- **Honest:** off-topic or unanswerable questions get "Not in the docs" with where to ask
  next, not a guess.
- **Production-minded:** secret scrubbing, rate limits, blue/green index builds with an
  evaluation gate, query log and feedback, a 35 KB embeddable widget.

## How it works

```
                 offline (nightly)                                  online (per question)
┌───────────────┐   ┌──────────────┐   ┌──────────────┐     ┌────────┐   ┌──────────────────┐
│ olake.io docs │──▶│ fetch        │──▶│ build        │     │ widget │──▶│ API              │
│ (sitemap)     │   │ crawl + git  │   │ parse, chunk │     └────────┘   │ scrub secrets    │
├───────────────┤   └──────────────┘   │ tag, symbols │                  │ analyse question │
│ olake         │                      └──────┬───────┘                  │ hybrid retrieval │
│ olake-ui      │                             ▼                          │ answer gate      │
│ olake-helm    │                      ┌──────────────┐  active version  │ LLM (streamed)   │
│ olake-fusion  │                      │ index        │─────────────────▶│ verify citations │
└───────────────┘                      │ embed, load, │  Postgres +      │ log + feedback   │
                                       │ eval, swap   │  pgvector        └──────────────────┘
                                       └──────────────┘
```

**Ingestion** (`apps/ingest`, CLI `olake-ingest`)

1. `fetch`: the docs and blog are crawled from olake.io through its sitemap (polite:
   honours robots.txt, skips unchanged pages by `lastmod`). Repos are shallow-cloned at a
   pinned ref; `olake` uses its latest release tag, so answers match what users run.
2. `build`: rendered pages are split at headings using the site's own anchor ids; Go is
   split per declaration with tree-sitter; YAML per key with its comments; Markdown by
   heading. Sections become ~300–700-token chunks with a breadcrumb, product, connector,
   topics, version and an exact citation URL (page anchor, or a GitHub permalink with line
   numbers). Config keys, env vars and error strings are extracted as exact-match symbols.
3. `index`: chunks are embedded (cached by content hash, so a rebuild only pays for what
   changed) and loaded into a new index version beside the live one. The new version goes
   live in one transaction, optionally only after passing the evaluation.

**Answering** (`apps/api`, CLI `olake-api`)

1. Secrets (passwords in URLs, keys, tokens) are removed before anything is logged or sent on.
2. Four retrievers are combined with Reciprocal Rank Fusion: vector
   similarity, English full-text, identifier phrases (`max_threads`, `v0.12.0`) and exact
   symbols (config keys; error strings contained in a pasted message). Official docs and
   code outrank blog posts; connector and product hints boost matching chunks.
3. A cheap gate stops clearly off-topic questions before any LLM call.
4. The LLM (via OpenRouter, or Claude / OpenAI directly) answers from numbered sources only, citing them; it replies
   `NOT_FOUND` when they don't contain the answer. With no key configured, the API falls
   back to showing the most relevant passages.
5. The answer is verified (citations, identifiers, links) and streamed to the widget as
   Server-Sent Events, with the cited sources.

## Quick start

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), Docker, Node 20+ (only to
rebuild the widget), git.

```bash
cp .env.example .env          # add OPENAI_API_KEY (embeddings) and OPENROUTER_API_KEY (answers)
make setup                    # Python deps + widget deps
make db                       # Postgres 16 + pgvector on localhost:5433
make migrate
make index                    # first run: ~1-3 minutes, mostly the crawl and embeddings
make dev                      # open http://localhost:8000
```

No API keys? Set `EMBEDDING_PROVIDER=hashing` in `.env`: everything runs offline with
word-matching retrieval and extractive answers. Useful for development and demos; not a
substitute for real embeddings.

Useful commands:

| Command | What it does |
| --- | --- |
| `uv run olake-ingest doctor` | Checks database, schema, vector size and the live index |
| `uv run olake-ingest inspect` | Writes `apps/ingest/out/viewer.html` to browse every chunk |
| `uv run olake-api search "question"` | Shows what retrieval finds, with scores and the gate decision |
| `uv run olake-api ask "question"` | Answers in the terminal, same pipeline as the API |
| `uv run olake-api eval --answers` | Runs the golden-set evaluation |

## Embedding the widget

```html
<script src="https://ask.your-domain.com/widget.js" defer></script>
```

The widget renders inside a Shadow DOM (the host page's CSS can't break it), follows the
reader's light/dark preference, works full-screen on phones, and keeps the conversation for
the browser session. Options: `data-api="https://..."` to point at a different API origin,
`data-open="true"` to open it on load. Set `ALLOWED_ORIGINS=https://olake.io` on the API.

## Calling the API

The widget streams answers from `POST /api/chat` (Server-Sent Events). Scripts and other
integrations can use `POST /ask` instead, which runs the same pipeline and returns one JSON
response:

```bash
curl -s http://localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question": "Which sync modes does the Postgres connector support?"}'
```

```json
{"answer": "The Postgres connector supports ... [1]",
 "sources": ["https://olake.io/docs/connectors/postgres/..."]}
```

`sources` lists the URLs the answer cites, in the order it cites them; it is `[]` when the
assistant declines (off-topic or not in the docs). Rate limits are shared with `/api/chat`;
errors come back as `429` (too many questions) or `503` (busy or unavailable).

## Evaluation

`eval/golden.yaml` holds 39 questions across setup, sync modes, configuration, catalogs,
Fusion maintenance, deployment and troubleshooting, plus off-topic and not-in-the-docs
questions. Each lists the sources that count as correct and, where useful, facts a correct
answer must contain.

| Metric | Meaning |
| --- | --- |
| hit@k, MRR | Is a correct source in the top k results; how high is the first one |
| gate false refusals | Answerable questions the pre-filter wrongly declines |
| answer facts | Answers containing every required fact (needs an LLM) |
| abstain rate | Unanswerable questions declined (needs an LLM) |
| grounded rate | Answers that cite at least one source |
| latency | p50/p95 for retrieval, full answers and first token |

Thresholds live in the golden file; `olake-api eval` exits non-zero when one is missed, so
it can gate changes. Baseline measured offline with the hashing embedder (the weakest
setting): **hit@5 0.88, MRR 0.77, gate false refusals 0.00, retrieval p50 ~90 ms**. Run it
again with OpenAI embeddings and an LLM key for the real numbers.

## Deploying

```bash
docker build -t ask-olake .
docker compose --profile app up -d                 # Postgres + API on :8000
docker compose run --rm ingest olake-ingest migrate
docker compose run --rm ingest                     # fetch + build + index
```

- **Refreshing the index:** `.github/workflows/refresh-index.yml` runs nightly: it builds a
  new version without activating it, evaluates it, and activates it only if it passes.
  A failed run leaves the previous version serving.
- **CI:** `.github/workflows/ci.yml` runs lint, the Python tests (against a real
  pgvector service) and the widget's typecheck, tests and build.
- **Behind a load balancer:** set `TRUST_PROXY_HEADERS=true` so rate limits apply per
  client rather than per proxy. Rate limits are per process; with several replicas, move
  them to Redis or the load balancer.
- **Privacy:** `query_log` stores scrubbed questions, answers, cited URLs and a salted hash
  of the client IP. Set `CLIENT_HASH_SALT`, and truncate the table on whatever retention
  schedule you publish.

## Configuration

All settings are environment variables (or `.env`); see `.env.example` for the full list.

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | local Docker database | Postgres 16 with pgvector |
| `EMBEDDING_PROVIDER` | `openai` | `openai` or `hashing` (offline, dev only) |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMS` | `text-embedding-3-small` / `1536` | Dims must match the schema |
| `LLM_PROVIDER` | `auto` | `openrouter`, `anthropic`, `openai`, `extractive`, or `auto` (picks by available key, OpenRouter first) |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | – / `google/gemini-2.5-flash-lite` | Answers through OpenRouter; the model must be allowed by the key's guardrail (`z-ai/glm-4.7-flash` and `deepseek/deepseek-v4.1-flash` are the alternatives). Embeddings still use `OPENAI_API_KEY` |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | |
| `OPENAI_CHAT_MODEL` | `gpt-4.1-mini` | Check OpenAI's current model list |
| `MIN_SIMILARITY` | provider default | Answer-gate threshold; tune with `olake-api eval` |
| `ALLOWED_ORIGINS` | `*` | Set to your site in production |
| `RATE_LIMIT_PER_MINUTE` / `RATE_LIMIT_BURST` | `10` / `5` | Per client |

Sources to index are configured in `apps/ingest/config/sources.yaml`.

## Project layout

```
packages/olake_shared/   Chunk contract, taxonomy, embedding providers (used by both apps)
apps/ingest/             olake-ingest: fetch, parse, chunk, embed, index; migrations
apps/api/                olake-api: retrieval, answer pipeline, HTTP API, evaluation
apps/widget/             Preact + TypeScript widget, built into apps/api/static/widget.js
eval/golden.yaml         Evaluation questions and thresholds
conftest.py              Test database fixture (tests skip if Postgres isn't running)
```

## Known limitations and next steps

- No cross-encoder reranker yet; fusion plus trust weights does the ranking.
- Fusion's Java code and external sources (Apache Iceberg spec, catalog docs) aren't
  indexed yet; the source config supports adding them.
- The answer verifier is deterministic (citations, identifiers, links). An LLM judge for
  sentence-level support belongs in the offline evaluation, not on every request.
- Rate limiting is in-process (see Deploying).
- The Docker image and the GitHub workflows were written but not run in the environment
  this was built in (no registry access); the Python and widget code paths they call are
  covered by the test suite.
