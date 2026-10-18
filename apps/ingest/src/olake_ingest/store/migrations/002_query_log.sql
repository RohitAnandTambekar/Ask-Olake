-- Written by the API: one row per question, plus optional user feedback.
-- Questions are stored after secret scrubbing; nothing here is needed to answer,
-- so the table can be truncated at any time (see README for retention).

create table query_log (
  id             uuid primary key,
  created_at     timestamptz not null default now(),
  index_version  int,
  question       text not null,
  answer         text,
  status         text not null check (status in ('answered', 'partial', 'abstained', 'blocked', 'error')),
  cited_urls     text[] not null default '{}',
  retrieved      jsonb,          -- chunk ids and scores, for debugging retrieval
  model          text,
  latency_ms     int,
  first_token_ms int,
  client_hash    text            -- salted hash of the client IP, for abuse analysis
);
create index query_log_created on query_log (created_at desc);
create index query_log_status on query_log (status, created_at desc);

create table feedback (
  id         bigserial primary key,
  query_id   uuid not null references query_log (id) on delete cascade,
  created_at timestamptz not null default now(),
  rating     smallint not null check (rating in (-1, 1)),
  comment    text check (length(comment) <= 2000)
);
create index feedback_query on feedback (query_id);
