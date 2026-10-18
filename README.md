# Ask OLake

A grounded chat assistant for [OLake](https://olake.io/). It answers questions about
**OLake Go** (replicating Postgres, MySQL, MongoDB, Oracle, MSSQL, DB2, Kafka and S3 into
Apache Iceberg or Parquet) and **OLake Fusion** (automated Iceberg table maintenance such as
compaction): setup, source configuration, sync modes, catalogs, table maintenance,
deployment and troubleshooting.

Answers come only from the official docs and the open-source code, every claim is cited,
and when the answer isn't there the assistant says so instead of guessing.

![A written answer with citation pins and the source rail](docs/screenshots/answer.png)

- **Grounded:** the model sees only retrieved passages and must cite them. Every answer is
  verified: citations must point at real sources, config keys it mentions must exist in
  them, and an answer without a valid citation is never shown.
- **Docs and code:** rendered docs pages (tabs, shared snippets and React components
  included) plus the Go source, Helm chart and Fusion docs. Pasting an error message finds
  the line of Go code that raises it.
- **Honest:** off-topic or unanswerable questions get "not in the docs" plus where to ask
  next.
- **Production-minded:** secret scrubbing, rate limits, blue/green index builds behind an
  evaluation gate, query log and feedback, a 34 KB (gzipped) embeddable widget.

---

## Contents

1. [Architecture](#architecture)
2. [Components](#components)
3. [Ingestion pipeline](#ingestion-pipeline)
4. [Answering pipeline](#answering-pipeline)
5. [HTTP API](#http-api)
6. [Database](#database)
7. [Configuration](#configuration)
8. [Running locally](#running-locally)
9. [Command reference](#command-reference)
10. [Evaluation](#evaluation)
11. [Deployment](#deployment)
12. [Security and privacy](#security-and-privacy)
13. [Project layout](#project-layout)
14. [Known limitations](#known-limitations)

---

## Architecture

The system has two halves that meet in one Postgres database.

```
            OFFLINE: build the knowledge base                 ONLINE: answer a question
            (apps/ingest, nightly or on demand)                (apps/api, per request)

 sources.yaml                                                     browser / script
     │                                                                  │
     ▼                                                                  ▼
┌──────────┐   ┌──────────────┐   ┌──────────────┐          ┌──────────────────────┐
│  fetch   │──▶│    build     │──▶│    index     │          │ widget  ─▶ /api/chat │
│ crawl    │   │ parse, chunk │   │ embed, load, │          │ scripts ─▶ /ask      │
│ olake.io │   │ tag, extract │   │ validate,    │          └──────────┬───────────┘
│ + clone  │   │ symbols      │   │ swap version │                     ▼
│ repos    │   └──────────────┘   └──────┬───────┘          ┌──────────────────────┐
└──────────┘    out/chunks.jsonl         │                  │ 1 scrub secrets      │
                out/symbols.jsonl        ▼                  │ 2 analyse question   │
                              ┌─────────────────────┐ reads │ 3 hybrid retrieval   │
                              │ Postgres + pgvector │◀──────│ 4 answer gate        │
                              │ index_versions      │       │ 5 LLM (OpenRouter)   │
                              │ chunks, symbols     │ writes│ 6 verify citations   │
                              │ embedding_cache     │◀──────│ 7 stream + log       │
                              │ query_log, feedback │       └──────────────────────┘
                              └─────────────────────┘
       embeddings: OpenAI text-embedding-3-small     answers: OpenRouter (or Claude/OpenAI)
```

- **Offline**, the ingestion pipeline turns the docs site and the GitHub repos into about
  4,300 citation-ready chunks, embeds them and publishes them as a new *index version*.
- **Online**, the API answers each question from the *active* index version only. A new
  version goes live atomically, so the API never sees a half-built index.

## Components

The repo is a [uv](https://docs.astral.sh/uv/) workspace with three Python packages and a
TypeScript widget.

| Component | Path | What it does |
| --- | --- | --- |
| **Shared contract** | `packages/olake_shared` | `Chunk`, `ChunkMeta` and `Symbol` models that ingestion writes and the API reads; the closed vocabularies (product, source type, connector, topic, symbol kind); the embedding providers. |
| **Ingestion** | `apps/ingest` | `olake-ingest` CLI: fetch → build → index, schema migrations, `doctor` and the chunk viewer. |
| **API** | `apps/api` | `olake-api` CLI and FastAPI server: retrieval, the answer pipeline, guardrails, HTTP endpoints, evaluation. |
| **Widget** | `apps/widget` | Preact + TypeScript chat widget, built into one file, `apps/api/static/widget.js`, which the API serves. |
| **Database** | `apps/ingest/src/olake_ingest/store/migrations` | Postgres 16 + pgvector schema shared by both apps. |
| **Evaluation set** | `eval/golden.yaml` | 39 questions with correct sources, required facts and pass thresholds. |

### `packages/olake_shared`

| Module | Contents |
| --- | --- |
| `models.py` | `Chunk` (id, content hash, `text` that is embedded = breadcrumb + body, `display_text` that is shown = body), `ChunkMeta` (product, source type, trust tier, connector, topics, version, title, breadcrumb, citation URL, repo, path, commit, line range), `Symbol` (config key, env var, error string, struct or func with its location). Chunk ids are `sha1(repo::path::locator)`, so they survive text edits. |
| `taxonomy.py` | Enums for every metadata value. A typo in a rule fails validation instead of silently creating a new category. |
| `embeddings.py` | `OpenAIEmbedder` (batched, retries 429/5xx) and `HashingEmbedder` (offline word hashing, for tests and demos). |

### `apps/ingest`

| Module | Role |
| --- | --- |
| `config/sources.yaml` | What to index: sources, refs, include/exclude globs, trust tiers. |
| `sources.py` | Loads and validates `sources.yaml`. |
| `fetch/` | `site.py` + `sitemap.py` crawl a site through its sitemap; `git.py` resolves refs and shallow-clones; `select.py` applies globs; `runner.py` writes `out/fetch.json`. |
| `parse/` | `html_page.py` (rendered Docusaurus pages), `go_code.py` (tree-sitter), `yaml_file.py`, `markdown_file.py`, dispatched by `files.py`. |
| `chunk/chunker.py` | Splits large sections and merges tiny ones into 120–700-token chunks. |
| `enrich/metadata.py` | Path-first rules for product, source type, version, connector and topics. |
| `pipeline/build.py` | Runs parse → chunk → enrich for every fetched file; writes `out/chunks.jsonl`, `out/symbols.jsonl`, `out/build.json`. |
| `store/` | `db.py` (connections, migrations), `index.py` (embed, load, validate, activate), `migrations/*.sql`. |
| `inspect/viewer.py` | Writes `out/viewer.html` to browse every chunk. |
| `cli.py` | `migrate`, `doctor`, `fetch`, `build`, `inspect`, `index`, `run`. |

### `apps/api`

| Module | Role |
| --- | --- |
| `main.py` | FastAPI app: endpoints, CORS, security headers, rate limiting, concurrency cap. |
| `answer.py` | `Answerer.run`: the whole per-question pipeline, emitted as events. |
| `guard.py` | Secret scrubbing, salted client hashing, token-bucket rate limiter. |
| `query.py` | Rule-based query analysis (no LLM call): follow-ups, connector hints, identifiers. |
| `retrieval.py` | Hybrid retrieval and Reciprocal Rank Fusion; the answer gate. |
| `prompts.py` | System prompt and how sources and history are laid out for the model. |
| `llm.py` | `OpenRouterLLM`, `AnthropicLLM`, `OpenAILLM` behind one streaming interface. |
| `verify.py` | Deterministic answer verification: citations, identifiers, links. |
| `store.py` | Query log and feedback writes (best effort: never breaks an answer). |
| `evaluate.py` | Golden-set evaluation, reports, optional activate-on-pass. |
| `config.py`, `db.py` | Settings from env / `.env`; the shared connection pool. |
| `static/` | `index.html` (demo page) and the built `widget.js`. |

### `apps/widget`

| File | Role |
| --- | --- |
| `main.tsx` | Mounts the widget in a Shadow DOM (host CSS can't break it); reads `data-api` / `data-open`. |
| `App.tsx` | Chat UI: messages, streaming text, citation pins, source rail, copy buttons, thumbs up/down. |
| `api.ts` | `POST /api/chat` and Server-Sent Events parsing; `POST /api/feedback`. |
| `markdown.ts` | Markdown rendering with `marked`, sanitised with DOMPurify. |
| `storage.ts` | Keeps the conversation for the browser session (falls back to memory). |

---

## Ingestion pipeline

Run all three stages with `olake-ingest run`, or each one on its own.

### What is indexed

Configured in `apps/ingest/config/sources.yaml`. Counts are from the current build.

| Source | Kind | Content | Ref | Trust tier | Docs → chunks |
| --- | --- | --- | --- | --- | --- |
| `docs` | site | olake.io `/docs/**` (rendered HTML) | live site | 1 | 123 → 892 |
| `guides` | site | olake.io blog and learn posts | live site | 3 | 88 → 1,443 |
| `olake-code` | git | `datazip-inc/olake` Go drivers, destinations, types, examples | latest release tag | 1 | 189 → 975 |
| `ui` | git | `datazip-inc/olake-ui` server (Go) and READMEs | `master` | 2 | 89 → 484 |
| `helm` | git | `datazip-inc/olake-helm` chart and Temporal worker | `master` | 1 | 53 → 252 |
| `fusion` | git | `datazip-inc/olake-fusion` docs and config | `master` | 2 | 39 → 278 |

Trust tiers become ranking weights at query time: official docs and code (1) outrank
secondary material (2), which outranks blog posts (3).

### 1. `fetch`

- **Sites** are crawled through the sitemap. The crawler honours `robots.txt`, sends a
  clear User-Agent, limits parallelism, and skips pages whose `lastmod` has not changed.
  Rendered HTML is used instead of MDX source because it already contains shared
  snippets, every tab panel, React component output and the site's real heading anchors,
  so citation anchors are correct by construction.
- **Repos** are shallow-cloned at a pinned ref. `latest-release` resolves to the highest
  `vX.Y.Z` tag (pre-releases ignored), so answers match the code users actually run.
- Every `include` glob must match at least one file or page, or fetch fails. That catches
  typos and upstream renames.
- Output: raw files under `apps/ingest/.work/`, a manifest at `out/fetch.json`.

### 2. `build`

Each file is parsed into sections, then chunked and tagged.

| Input | Parser | Section boundary | Extracted symbols |
| --- | --- | --- | --- |
| Rendered docs page | `html_page.py` | `h2`–`h4` headings that carry an `id` | – |
| Go | `go_code.py` (tree-sitter) | each top-level declaration; long ones in 90-line windows with 10 lines overlap | `json:"…"` tags → `config_key`; strings passed to `errors.New` / `fmt.Errorf` / logger errors → `error_string`; `os.Getenv` → `env_var`; declared names → `struct` / `func` |
| YAML | `yaml_file.py` | each top-level key, with the comments above it | – |
| Markdown | `markdown_file.py` | headings | – |

The chunker then sizes the sections:

- larger than **700 tokens**: split at blank lines, never inside a code block; an
  oversized table repeats its header, oversized code re-opens its fence;
- smaller than **120 tokens**: merged with siblings under the same parent heading and tab.

Each chunk gets metadata from path-first rules (`/docs/connectors/postgres/` →
`postgres`) plus keyword rules for topics, a breadcrumb that is embedded with the text
(`OLake Go › Connectors › Postgres › CDC Setup`), and an exact citation URL: a page
`#anchor`, or a GitHub permalink at the fetched commit with line numbers (`#L40-L72`).

Output: `out/chunks.jsonl`, `out/symbols.jsonl`, `out/build.json` (per-source counts and
parse errors). `olake-ingest inspect` writes `out/viewer.html` to browse the chunks.

### 3. `index`

A blue/green publish:

1. Insert an `index_versions` row with status `building`.
2. Embed the chunks with OpenAI. Vectors are cached in `embedding_cache` by content hash,
   so a rebuild only pays for text that changed.
3. Bulk-load chunks and symbols under the new version id.
4. Validate: row count matches, no missing vectors. On failure the version is retired and
   the build stops.
5. Mark it `ready` and, unless `--no-activate`, switch it to `active` and the previous one
   to `retired` in one transaction. A unique partial index guarantees one active version.
6. Delete retired versions beyond the newest two (their rows cascade).

---

## Answering pipeline

`Answerer.run` in `apps/api/src/olake_api/answer.py` handles one question end to end.

### 1. Scrub secrets (`guard.py`)

People paste connection strings and config files. Before anything is logged, embedded or
sent to a model, these are replaced with `[REDACTED]`: private keys, AWS keys,
`sk-…`/`ghp_…`/Slack tokens, JWTs, bearer tokens, passwords in URLs
(`postgres://user:PASS@host`) and `password=` / `"secret_key": …` pairs. The user is told
and asked to rotate them.

### 2. Analyse the question (`query.py`)

Rule-based, so there is no extra LLM call on the hot path:

- **Follow-ups**: a short question or one starting with "what about", "and for", … is
  joined to the previous user question before searching ("what about MySQL?").
- **Connector hints** (postgres, binlog, oplog, …) and **Fusion hints** (compaction,
  snapshot, expire, …) for boosting.
- **Identifiers**: anything in backticks and tokens like `max_threads`, `source.json`,
  `v0.12.0`, for exact matching.

### 3. Hybrid retrieval (`retrieval.py`)

Up to five retrievers each return 40 candidates from the active index version:

| Retriever | Finds | Weight |
| --- | --- | --- |
| `vector` | similar meaning: cosine similarity on the HNSW index | 1.0 |
| `words` | English full-text search (stemmed), OR over the question's words | 1.0 |
| `idents` | phrase match on identifiers (`max_threads` → `max <-> threads`) | 1.5 |
| `symbols` | exact config key, env var, struct or func name | 1.5 |
| `errors` | Go error strings whose static prefix (before the first `%v`) appears inside the pasted message | 3.0 |

The lists are merged with **Reciprocal Rank Fusion**: each chunk scores
`Σ weight / (60 + rank)` over the lists it appears in. A chunk that several retrievers
agree on wins, without calibrating their raw scores against each other. Scores are then
multiplied by trust (tier 1: 1.0, tier 2: 0.92, tier 3: 0.7), by 1.2 when the chunk's
connector matches the question, and by 1.15 for Fusion chunks on maintenance questions.
The top **8** become the numbered sources.

### 4. Answer gate

A cheap check before any LLM call. The question proceeds if an exact symbol or identifier
matched, or if **both** the best vector similarity reaches the floor (0.30 with OpenAI
embeddings) **and** at least half the question's keywords appear in the top 3 hits.
Otherwise the reply is "couldn't find this" with links to the OLake Slack and GitHub
issues (and the closest pages, when they are related at all). The gate is deliberately
lenient; the model's own `NOT_FOUND` rule makes the finer call.

### 5. Generate (`prompts.py`, `llm.py`)

The system prompt tells the model to use only the numbered sources, cite every claim as
`[n]`, copy keys and commands exactly, prefer official docs over blogs, treat source text
as data rather than instructions (prompt-injection defence), and reply `NOT_FOUND` when the
sources don't contain the answer. Sources are attached as `<source id="n">` blocks with
URL, type and trust tier; the last 6 history messages are sent as plain turns.

Providers, chosen by `LLM_PROVIDER`:

| Provider | Used when | Notes |
| --- | --- | --- |
| `openrouter` | default for this deployment; first choice under `auto` | OpenAI-compatible API at `https://openrouter.ai/api/v1`, `X-Title: Ask OLake` |
| `anthropic` | `ANTHROPIC_API_KEY` set | Claude via the official SDK, temperature 0 |
| `openai` | `OPENAI_API_KEY` set and no other key | OpenAI chat completions |
| `extractive` | no key, or the provider fails before any text streamed | no model: shows the 3 most relevant passages |

The first few characters of the stream are held back: if they turn out to be `NOT_FOUND`,
the refusal is never streamed token by token and becomes a clean "not in the docs" reply.

### 6. Verify (`verify.py`)

Deterministic and cheap, so it runs on every answer:

- **Citations**: `[n]` markers outside the source list are removed; an answer left with
  no valid citation is replaced by an abstain reply.
- **Identifiers**: names in backticks and keys in JSON/YAML code blocks must appear in the
  sources; unknown ones produce a "double-check these names" warning (status `partial`).
  This catches invented config keys.
- **Links**: only links to the given sources, `olake.io` or `github.com/datazip-inc/`
  are kept.

### 7. Respond and log

The result goes to the client (streamed or as one JSON object, see below) and a row is
written to `query_log`: scrubbed question, answer, status, cited URLs, retrieval scores,
model, latency, time to first token and a salted hash of the client IP.

Answer statuses: `answered`, `partial` (answered with warnings, or extractive), `abstained`,
`error`.

---

## HTTP API

Interactive docs: `GET /api/docs`. OpenAPI schema: `GET /openapi.json` (importable into
Postman).

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/ask` | One question → one JSON answer. For scripts and integrations. |
| `POST` | `/api/chat` | Streamed answer (Server-Sent Events) with history. Used by the widget. |
| `GET` | `/api/model` | Which model generates answers. |
| `POST` | `/api/feedback` | Thumbs up/down on an answer. |
| `GET` | `/api/health` | Readiness: database, active index, model match. |
| `GET` | `/`, `/widget.js` | Demo page and the embeddable widget. |

### `POST /ask`

```bash
curl -s http://localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question": "What MySQL server settings are required for CDC?"}'
```

```json
{
  "answer": "For CDC, MySQL needs `log_bin=ON`, `binlog_format=ROW` ... [1][2]",
  "sources": [
    "https://olake.io/docs/connectors/mysql/#cdc-prerequisite",
    "https://olake.io/docs/connectors/mysql/setup/generic/#1-configure-mysql-binary-logging"
  ]
}
```

`sources` holds the URLs the answer cites, in first-use order, without duplicates. It is
`[]` when the assistant declines. `question` is 1–4,000 characters.

### `POST /api/chat`

```json
{
  "message": "what about MySQL?",
  "history": [
    {"role": "user", "content": "Which sync modes does the Postgres connector support?"},
    {"role": "assistant", "content": "..."}
  ]
}
```

The response is `text/event-stream`:

| Event | Data | When |
| --- | --- | --- |
| `meta` | `{query_id, notices}` | first, always; `notices` warns about scrubbed credentials |
| `sources` | `{sources: [{n, title, url, breadcrumb, source_type, product, trust_tier}]}` | after retrieval |
| `delta` | `{text}` | streamed answer text |
| `final` | `{status, answer, citations, warnings}` | last; `answer` is the verified text and replaces the streamed text |
| `error` | `{message}` | instead of `final` when the answer fails |

### `GET /api/model`

```json
{"provider": "openrouter", "model": "google/gemini-2.5-flash-lite", "embedding_model": "text-embedding-3-small"}
```

`model` is `null` in extractive mode.

### `POST /api/feedback`

`{"query_id": "<uuid from meta>", "rating": 1, "comment": "optional"}` → `204`. `rating` is
`1` or `-1`.

### `GET /api/health`

`200` with `{status: "ok", llm, embedding_model, index_version, chunks, index_built}`, or
`503` with a `reason` when there is no active index or it was built with a different
embedding model.

### Errors and limits

| Status | Meaning |
| --- | --- |
| `422` | invalid body (empty or too long question, unknown field, bad history role) |
| `429` | rate limit: 10 questions per minute per client, bursts of 5 (`/ask` and `/api/chat` share it) |
| `503` | all answer slots busy (8 concurrent by default) or the index/model is unavailable |

---

## Database

Postgres 16 with pgvector. Schema in `apps/ingest/src/olake_ingest/store/migrations/`.

| Table | Contents |
| --- | --- |
| `index_versions` | one row per index build: embedding model, dims, status (`building`, `ready`, `active`, `retired`), manifest. At most one `active`. |
| `chunks` | chunk text, display text, metadata (JSONB), filter columns (product, connector, source type, trust tier), citation URL, `vector(1536)` embedding, and two generated full-text columns: `tsv_en` (English, stemmed) and `tsv_ident` (simple, keeps `max_threads` intact). Indexes: HNSW on the embedding, GIN on both text columns. |
| `symbols` | config keys, env vars, error strings, structs and funcs, with file, line and owning chunk. |
| `embedding_cache` | vectors keyed by `(content_hash, model)`, reused across builds. |
| `query_log` | one row per question (scrubbed), with answer, status, citations, retrieval scores, latency. |
| `feedback` | ratings and comments linked to `query_log`. |

`query_log` and `feedback` aren't needed to answer and can be truncated at any time.

---

## Configuration

All settings are environment variables, read from the process environment or the repo-root
`.env` (copy `.env.example`). `.env` is gitignored and excluded from the Docker image.

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql://olake:olake@localhost:5433/ask_olake` | Full connection string including user and password; add `?sslmode=require` for managed databases |
| `EMBEDDING_PROVIDER` | `openai` | `openai`, or `hashing` (offline, dev only) |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMS` | `text-embedding-3-small` / `1536` | Must match the index; dims must match `vector(1536)` in the schema |
| `OPENAI_API_KEY` | – | Embeddings (ingestion and every question) |
| `LLM_PROVIDER` | `auto` | `openrouter`, `anthropic`, `openai`, `extractive`, or `auto` (OpenRouter → Anthropic → OpenAI → extractive, by available key) |
| `OPENROUTER_API_KEY` | – | Answer generation via OpenRouter |
| `OPENROUTER_MODEL` | `google/gemini-2.5-flash-lite` | Must be allowed by the key's guardrail; `z-ai/glm-4.7-flash` and `deepseek/deepseek-v4.1-flash` also are |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | |
| `OPENROUTER_SITE_URL` | – | Optional; sent as `HTTP-Referer` for OpenRouter attribution |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | – / `claude-sonnet-5-5` | Alternative provider |
| `OPENAI_CHAT_MODEL` | `gpt-4.1-mini` | Alternative provider |
| `LLM_MAX_OUTPUT_TOKENS` / `LLM_TIMEOUT_SECONDS` | `1200` / `60` | |
| `TOP_K` / `CANDIDATES` | `8` / `40` | Sources given to the model / candidates per retriever |
| `MIN_SIMILARITY` | provider default (0.30 OpenAI, 0.12 hashing) | Answer-gate floor; tune with `olake-api eval` |
| `ALLOWED_ORIGINS` | `*` | Comma-separated origins allowed to call the API from a browser |
| `RATE_LIMIT_PER_MINUTE` / `RATE_LIMIT_BURST` | `10` / `5` | Per client, per process |
| `MAX_CONCURRENT_ANSWERS` | `8` | Per process |
| `TRUST_PROXY_HEADERS` | `false` | Use the first `X-Forwarded-For` address as the client IP |
| `CLIENT_HASH_SALT` | `change-me` | Salt for the client IP hash in `query_log`; set a random value |
| `SITE_FETCH_BASE` | – | Crawl a local docs build instead of olake.io; citations still use olake.io |

---

## Running locally

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), Docker, git, and Node 20+
(only to rebuild the widget).

```bash
cp .env.example .env     # set OPENAI_API_KEY (embeddings) and OPENROUTER_API_KEY (answers)
make setup               # Python + widget dependencies
make db                  # Postgres 16 + pgvector on localhost:5433
make migrate             # create the schema
make index               # fetch + build + index; first run ~1-3 minutes
make dev                 # API and demo page on http://localhost:8000
```

Without `make` (e.g. Windows PowerShell):

```powershell
uv sync; cd apps/widget; npm ci; cd ../..
docker compose up -d db
uv run olake-ingest migrate
uv run olake-ingest run
uv run olake-api serve --port 8000
```

On Windows, set `PYTHONUTF8=1` before the CLI commands that print rich output
(`olake-api ask`, `search`); the default console encoding can't print some characters.

No API keys? Set `EMBEDDING_PROVIDER=hashing` and leave the LLM keys empty: everything
runs offline with word-matching retrieval and extractive answers. Good for development, not
a substitute for real embeddings. (The index must be rebuilt after switching embedders.)

Tests: `make test` (Python tests need the Postgres container; they skip otherwise) and
`make lint`.

---

## Command reference

| Command | What it does |
| --- | --- |
| `uv run olake-ingest migrate` | Create or upgrade the database schema |
| `uv run olake-ingest doctor` | Check the database, schema, vector size and the active index |
| `uv run olake-ingest fetch [--only NAME]` | Crawl sites and clone repos |
| `uv run olake-ingest build` | Parse and chunk everything fetched |
| `uv run olake-ingest inspect` | Write `apps/ingest/out/viewer.html` to browse chunks |
| `uv run olake-ingest index [--no-activate]` | Embed and load a new index version |
| `uv run olake-ingest run` | fetch + build + index |
| `uv run olake-api serve --port 8000` | Run the API |
| `uv run olake-api ask "question"` | Answer in the terminal, same pipeline as the API |
| `uv run olake-api search "question"` | Show retrieval results, scores and the gate decision |
| `uv run olake-api eval [--answers] [--version latest-ready] [--activate-on-pass]` | Run the golden-set evaluation |
| `make widget` | Rebuild `apps/api/static/widget.js` |

---

## Evaluation

`eval/golden.yaml` holds 39 questions across setup, sync modes, configuration, catalogs,
Fusion maintenance, deployment and troubleshooting, plus off-topic and not-in-the-docs
questions. Each lists the URL fragments that count as a correct source and, where useful,
facts a correct answer must contain.

| Metric | Meaning |
| --- | --- |
| hit@k, MRR | Is a correct source in the top k; how high is the first one |
| gate false refusals | Answerable questions the pre-filter wrongly declines |
| answer facts | Answers containing every required fact (`--answers`) |
| abstain rate | Unanswerable questions declined (`--answers`) |
| grounded rate | Answers citing at least one source |
| latency | p50/p95 for retrieval, full answers and first token |

Thresholds live in the golden file; `olake-api eval` writes `eval/report.json` and
`eval/report.md` and exits non-zero when one is missed, so it can gate changes. Baseline
with the offline hashing embedder (the weakest setting): hit@5 0.88, MRR 0.77, gate false
refusals 0.00, retrieval p50 ~90 ms. `--answers` calls the LLM for all 39 questions, so it
spends OpenRouter budget.

---

## Deployment

### Docker

One image serves both roles: the API by default, ingestion with `olake-ingest run`. The
multi-stage `Dockerfile` builds the widget with Node, installs the Python workspace with
uv, runs as a non-root user and has a health check on `/api/health`.

```bash
docker build -t ask-olake .
docker compose --profile app up -d                 # Postgres + API on :8000
docker compose run --rm ingest olake-ingest migrate
docker compose run --rm ingest                     # fetch + build + index
```

### DigitalOcean App Platform

```
GitHub (main) ──build Dockerfile──▶ App Platform web service :8000 ──▶ Managed PostgreSQL 16
                                                                         (pgvector)
```

1. **Database**: create a Managed PostgreSQL 16 cluster (`ask-olake-db`). DigitalOcean's
   managed Postgres supports pgvector; App Platform "dev databases" do not.
2. **Load the index** from your machine (add your IP to the cluster's trusted sources),
   *before* the first deploy, because `/api/health` returns 503 until an index is active:
   ```bash
   DATABASE_URL='postgresql://doadmin:<password>@<host>:25060/defaultdb?sslmode=require' \
     uv run olake-ingest migrate && uv run olake-ingest run && uv run olake-ingest doctor
   ```
3. **Create the app from `.do/app.yaml`**: one web service built from the Dockerfile on
   port 8000 with health check `/api/health`, plus the attached database. Paste it into
   *App → Settings → App Spec*, or run `doctl apps create --spec .do/app.yaml`.
4. **Set the secrets** in the console (encrypted): `OPENAI_API_KEY`, `OPENROUTER_API_KEY`,
   `CLIENT_HASH_SALT`. `DATABASE_URL` is `${ask-olake-db.DATABASE_URL}`, which App Platform
   fills in with the full connection string (user and password included) at runtime.
5. **Check** `/api/health` (`"status": "ok"`) and `/api/model`.

Notes:

- **Use the spec, not auto-detection.** Auto-detection mistakes `packages/` (the shared
  Python library) for a DigitalOcean Functions project, adds a Functions component and
  the build fails asking for `project.yml`.
- In the spec, `databases[].name` is the prefix used in `${ask-olake-db.DATABASE_URL}`;
  `cluster_name` must match the cluster's name in the console.
- Put the app in the same region as the database.

### Refreshing the index

`.github/workflows/refresh-index.yml` runs nightly (and on demand): fetch → build →
`index --no-activate` → `eval --version latest-ready --activate-on-pass`. A version that
fails evaluation never goes live; the previous one keeps serving. It needs repository
secrets `DATABASE_URL`, `OPENAI_API_KEY` and `OPENROUTER_API_KEY`, and the database must
accept connections from GitHub's runners (their IPs change between runs).

### CI

`.github/workflows/ci.yml` runs ruff, the Python tests against a pgvector service, and the
widget's typecheck, tests and build.

---

## Security and privacy

- Credentials in questions are scrubbed before logging, embedding or calling a model, and
  the user is warned.
- Retrieved text is treated as data in the prompt; instructions inside sources are ignored.
- Answers keep only links to the cited sources and OLake's own sites; the widget sanitises
  rendered Markdown and opens links in a new tab without access to the host page.
- `query_log` stores scrubbed questions and a salted hash of the client IP, never the IP.
  Set `CLIENT_HASH_SALT`, and truncate the table on whatever retention schedule you publish.
- Secrets live in `.env` locally (gitignored, excluded from the image) and as encrypted
  variables on App Platform. Never put real keys in `.env.example` or `.do/app.yaml`.
- Responses carry `X-Content-Type-Options: nosniff` and a strict referrer policy; CORS is
  limited to `ALLOWED_ORIGINS`.

---

## Project layout

```
.do/app.yaml                  DigitalOcean App Platform spec
.github/workflows/            ci.yml, refresh-index.yml
packages/olake_shared/        chunk contract, taxonomy, embedding providers
apps/ingest/
  config/sources.yaml         what to index
  src/olake_ingest/           fetch/ parse/ chunk/ enrich/ pipeline/ store/ inspect/ cli.py
  tests/
apps/api/
  src/olake_api/              main.py answer.py retrieval.py query.py prompts.py llm.py
                              verify.py guard.py store.py evaluate.py config.py db.py cli.py
  static/                     index.html, widget.js (built)
  tests/
apps/widget/src/              main.tsx App.tsx api.ts markdown.ts storage.ts styles.css
eval/golden.yaml              evaluation questions and thresholds
docs/screenshots/             README images
conftest.py                   test database fixture (tests skip if Postgres isn't running)
Dockerfile, docker-compose.yml, Makefile, pyproject.toml, uv.lock
```

---

## Known limitations

- **Client IP behind a load balancer**: on App Platform every request arrives from the
  load balancer, so without reading DigitalOcean's `do-connecting-ip` header all visitors
  share one rate-limit bucket. `TRUST_PROXY_HEADERS=true` trusts the first
  `X-Forwarded-For` entry, which clients can forge.
- **Per-process limits**: rate limits and the concurrency cap live in each worker (the
  image runs 2). With several instances, move rate limiting to Redis or the load balancer.
- **No reranker**: fusion plus trust weights do the ranking; a cross-encoder would help on
  broad questions where blog chunks (the largest source) compete with the docs.
- **Not indexed yet**: Fusion's Java code and external sources (Apache Iceberg spec,
  catalog docs). `sources.yaml` and the `external` product support adding them.
- **Deterministic verifier**: it checks citations, identifiers and links, not whether each
  sentence is supported. An LLM judge belongs in offline evaluation, not on every request.
- **Windows**: some ingest tests read fixtures with the console's default encoding and
  fail unless `PYTHONUTF8=1` is set; the symlink test in `test_select.py` also needs
  Developer Mode (or admin rights) to create symlinks.
