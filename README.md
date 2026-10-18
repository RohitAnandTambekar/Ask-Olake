# Ask OLake

A chat assistant for [OLake](https://olake.io/) that answers questions from the official docs
and the open-source code. It covers **OLake Go** (replicating Postgres, MySQL, MongoDB, Oracle,
MSSQL, DB2, Kafka and S3 into Apache Iceberg or Parquet) and **OLake Fusion** (Iceberg table
maintenance such as compaction).

Every answer cites its sources. When the docs don't cover a question, it says so instead of
guessing.

![A written answer with citation pins and the source rail](docs/screenshots/answer.png)

## How it works

```
 OFFLINE (build the index)                          ONLINE (answer a question)

 olake.io docs ─┐                                   widget ──▶ POST /api/chat (streamed)
 GitHub repos ──┼─▶ fetch ─▶ build ─▶ index ─┐      script ──▶ POST /ask      (JSON)
                ┘                            ▼                       │
                                    Postgres + pgvector ◀────────────┘
```

**Building the index** (`olake-ingest`)

1. **Fetch** the docs site through its sitemap and clone the OLake repos at a pinned ref.
2. **Build** ~4,300 chunks: docs split at headings, Go code split per function with
   tree-sitter. Each chunk gets a citation URL (page anchor or GitHub line link) and tags
   such as product and connector. Config keys and error strings are saved as exact-match
   symbols.
3. **Index**: embed the chunks with OpenAI and load them as a new version, switched live in
   one transaction. Unchanged chunks reuse cached embeddings.

**Answering a question** (`olake-api`)

1. **Scrub** passwords, keys and tokens from the question.
2. **Retrieve** with five searches (meaning, words, identifiers like `max_threads`, exact config
   keys, pasted error strings), merged, then re-ranked by similarity. Chunks about a different connector are
   dropped when the question names one.
3. **Gate** clearly off-topic questions before calling a model.
4. **Generate** with an LLM that may only use the numbered sources and must cite them.
5. **Verify** citations, config keys and links, then stream the answer and log it.

## Quick start

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), Docker, git. Node 20+ only to
rebuild the widget.

```bash
cp .env.example .env     # add OPENAI_API_KEY (embeddings) and OPENROUTER_API_KEY (answers)
make setup               # install dependencies
make db                  # Postgres + pgvector on localhost:5433
make migrate             # create tables
make index               # fetch, build and index (~1-3 minutes)
make dev                 # http://localhost:8000
```

No `make` (Windows PowerShell)? Run the commands from the `Makefile` directly, e.g.
`uv run olake-ingest run` and `uv run olake-api serve --port 8000`. Set `PYTHONUTF8=1` so the
CLI can print its tables.

## API

Interactive docs at `/api/docs`.

| Method | Path | Returns |
| --- | --- | --- |
| `POST` | `/ask` | `{"answer": "...", "sources": ["<cited url>", ...]}` for `{"question": "..."}` |
| `POST` | `/api/chat` | The same answer as a Server-Sent Events stream, with chat history (used by the widget) |
| `GET` | `/models` | Model ids the OpenRouter key is allowed to use, as OpenRouter lists them |
| `GET` | `/api/model` | The answer model, its fallbacks and the embedding model |
| `GET` | `/version` | `{"commit", "deployment"}` read from `VERCEL_GIT_COMMIT_SHA` and `VERCEL_DEPLOYMENT_ID` |
| `POST` | `/api/feedback` | Records a thumbs up or down on an answer |
| `GET` | `/api/health` | Database, active index and model status |

```bash
curl -s http://localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question": "What MySQL settings does OLake need for CDC?"}'
```

`sources` lists only the URLs the answer cites, and is `[]` when the assistant declines.
Questions are rate-limited to 10 per minute per client (`429` when exceeded).

To embed the widget on a site:

```html
<script src="https://<your-api-host>/widget.js" defer></script>
```

## Configuration

Set in `.env` locally, or as environment variables when deployed. See `.env.example`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | local Docker database | Postgres connection string, including user and password |
| `OPENAI_API_KEY` | – | Embeddings, for indexing and for every question |
| `OPENROUTER_API_KEY` | – | Answer generation |
| `OPENROUTER_MODEL` | `z-ai/glm-4.7-flash` | First model tried |
| `OPENROUTER_FALLBACK_MODELS` | `google/gemini-2.5-flash-lite,deepseek/deepseek-v4.1-flash` | Tried in order if the first fails |
| `LLM_PROVIDER` | `auto` | `openrouter`, `anthropic`, `openai`, or `extractive` (no model: shows passages) |
| `ALLOWED_ORIGINS` | `*` | Sites allowed to call the API from a browser |
| `RATE_LIMIT_PER_MINUTE` | `10` | Questions per client per minute |
| `CLIENT_HASH_SALT` | `change-me` | Salt for hashing client IPs in the query log; set a random value |

What gets indexed is set in `apps/ingest/config/sources.yaml`.

## Commands

| Command | What it does |
| --- | --- |
| `uv run olake-ingest run` | Fetch, build and index |
| `uv run olake-ingest doctor` | Check the database and the active index |
| `uv run olake-ingest inspect` | Write `apps/ingest/out/viewer.html` to browse all chunks |
| `uv run olake-api serve` | Run the API |
| `uv run olake-api ask "..."` | Answer a question in the terminal |
| `uv run olake-api search "..."` | Show what retrieval finds, with scores |
| `uv run olake-api eval` | Score retrieval on `eval/golden.yaml` (add `--answers` to score answers too) |
| `make test` / `make lint` | Tests (Python tests need the Postgres container) and lint |

## Evaluation

`eval/golden.yaml` holds 39 questions with the sources that count as correct. On the current
index, the right source is ranked first for 88% of answerable questions and is in the top 3 for
all of them (MRR 0.94). `olake-api eval` fails when a threshold in the file is missed, so it can
block a bad index or change.

## Deployment

The `Dockerfile` builds one image with the API and the widget.

**DigitalOcean App Platform** (current host):

1. Create a Managed PostgreSQL 16 cluster. It supports pgvector.
2. Load the index into it from your machine before the first deploy:
   `DATABASE_URL=<cluster url> uv run olake-ingest migrate`, then `uv run olake-ingest run`.
3. Create the app from this repo as a **web service** built from the `Dockerfile`, on port
   `8000`, with health check `/api/health`, and attach the database. Remove any *Functions*
   component auto-detection adds: it mistakes the `packages/` folder for a Functions project.
4. Set `DATABASE_URL=${<db-name>.DATABASE_URL}` and the variables above. Mark the API keys as
   encrypted.
5. For `/version`, set `VERCEL_GIT_COMMIT_SHA=${_self.COMMIT_HASH}`. App Platform has no
   deployment ID, so `deployment` is `null` there.

`.github/workflows/refresh-index.yml` rebuilds the index nightly and only switches to the new
version if it passes evaluation. `.github/workflows/ci.yml` runs lint and tests.

## Project layout

```
apps/ingest/        olake-ingest: fetch, parse, chunk, embed, index; database migrations
apps/api/           olake-api: retrieval, answers, HTTP API, evaluation
apps/widget/        Preact chat widget, built into apps/api/static/widget.js
packages/           shared chunk models, taxonomy and embedding clients
eval/golden.yaml    evaluation questions
```

## Known limitations

- **`z-ai/glm-4.7-flash` can return empty answers.** It is a reasoning model and can use its
  whole output limit thinking. OpenRouter only falls back on errors, not empty replies.
  Until this is handled, set `OPENROUTER_MODEL=google/gemini-2.5-flash-lite`.
- Questions about unsupported features (e.g. Cassandra, Delta Lake) get "couldn't find"
  rather than a clear "not supported".
- Pages that are mostly tables, such as query-engine compatibility, are hard to retrieve.
- Rate limits are per process, so they don't hold across several instances.
- The index refreshes nightly, so docs changes can take up to a day to appear.
