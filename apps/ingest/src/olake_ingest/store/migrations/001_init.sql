create extension if not exists vector;

-- One row per build of the index. Exactly one is 'active' at a time;
-- the API will read only that one.
create table index_versions (
  id              serial primary key,
  created_at      timestamptz not null default now(),
  embedding_model text not null,
  dims            int  not null,
  status          text not null check (status in ('building', 'ready', 'active', 'retired')),
  manifest        jsonb
);
create unique index one_active_index_version on index_versions ((status)) where status = 'active';

create table chunks (
  id            text     not null,
  index_version int      not null references index_versions (id) on delete cascade,
  content_hash  text     not null,
  text          text     not null,
  display_text  text     not null,
  meta          jsonb    not null,
  product       text     not null,
  connector     text,
  source_type   text     not null,
  trust_tier    smallint not null,
  url           text     not null,
  embedding     vector(1536),
  -- 'english' stems prose; 'simple' keeps identifiers like max_threads intact
  tsv_en        tsvector generated always as (to_tsvector('english', text)) stored,
  tsv_ident     tsvector generated always as (to_tsvector('simple', text)) stored,
  primary key (id, index_version)
);
create index chunks_embedding_hnsw on chunks using hnsw (embedding vector_cosine_ops);
create index chunks_tsv_en on chunks using gin (tsv_en);
create index chunks_tsv_ident on chunks using gin (tsv_ident);
create index chunks_filters on chunks (index_version, product, connector);

create table symbols (
  name          text not null,
  kind          text not null
                check (kind in ('config_key', 'flag', 'env_var', 'error_string', 'struct', 'func')),
  repo          text not null,
  path          text not null,
  line          int  not null,
  chunk_id      text,
  index_version int  not null references index_versions (id) on delete cascade
);
create index symbols_name on symbols (lower(name));

-- Embeddings keyed by text hash, so rebuilding the index only pays for changed chunks.
create table embedding_cache (
  content_hash text not null,
  model        text not null,
  embedding    vector(1536) not null,
  created_at   timestamptz not null default now(),
  primary key (content_hash, model)
);
