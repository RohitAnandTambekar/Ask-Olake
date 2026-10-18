"""Evaluate the assistant against eval/golden.yaml.

Retrieval (always, cheap, no LLM):
  hit@k   share of answerable questions with a relevant source in the top k
  MRR     mean of 1/rank of the first relevant source
  gate    how often the pre-filter refuses answerable questions (false refusals) and
          how often it already declines the unanswerable ones
Answers (--answers, uses the configured LLM; extractive mode if none):
  facts      share of answerable questions whose answer contains every must_contain fact
  abstain    share of unanswerable questions declined
  grounding  share of answers that cite at least one source
  latency    p50/p95 total time and time to first token

Writes eval/report.json and eval/report.md, and fails when a threshold is missed, so it
can gate changes to prompts, retrieval or the index in CI.
"""

import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

from olake_api.config import REPO_ROOT, get_settings
from olake_api.query import analyze
from olake_api.retrieval import search

DEFAULT_GOLDEN = REPO_ROOT / "eval" / "golden.yaml"
KS = (1, 3, 5, 8)


def _first_relevant_rank(urls: list[str], relevant: list[str]) -> int | None:
    for rank, url in enumerate(urls, start=1):
        if any(r in url for r in relevant):
            return rank
    return None


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, round(q * (len(values) - 1)))]


def resolve_version(conn, version: str) -> int:
    """'active', 'latest-ready' (newest built but not live) or a version id."""
    if version == "active":
        row = conn.execute("select id from index_versions where status = 'active'").fetchone()
    elif version == "latest-ready":
        row = conn.execute(
            "select id from index_versions where status = 'ready' order by id desc limit 1"
        ).fetchone()
    else:
        row = conn.execute(
            "select id from index_versions where id = %s", (int(version),)
        ).fetchone()
    if row is None:
        raise ValueError(f"no index version matches {version!r}")
    return row[0]


def activate(conn, version_id: int) -> None:
    with conn.transaction():
        conn.execute("update index_versions set status = 'retired' where status = 'active'")
        conn.execute("update index_versions set status = 'active' where id = %s", (version_id,))


def main(
    *,
    answers: bool,
    golden_path: str | None,
    console: Console,
    version: str = "active",
    activate_on_pass: bool = False,
) -> bool:
    from olake_api.main import get_answerer  # imported late: builds the pool and providers

    golden = yaml.safe_load(Path(golden_path or DEFAULT_GOLDEN).read_text(encoding="utf-8"))
    thresholds = golden.get("thresholds", {})
    questions = golden["questions"]
    answerer = get_answerer()
    settings = get_settings()
    with answerer.pool.connection() as conn:
        version_id = resolve_version(conn, version)
    answerer.index_version = version_id
    answerer.check_ready()
    console.print(f"Evaluating index version {version_id}")

    rows: list[dict] = []
    for q in questions:
        answerable = q.get("answerable", True)
        analysis = analyze(q["question"])
        started = time.monotonic()
        vector = answerer.embedder.embed([analysis.search_text])[0]
        with answerer.pool.connection() as conn:
            result = search(
                conn,
                analysis,
                vector,
                top_k=max(KS),
                candidates=settings.candidates,
                version=version_id,
            )
        retrieval_ms = (time.monotonic() - started) * 1000
        urls = [h.url for h in result.hits]
        row = {
            "id": q["id"],
            "category": q["category"],
            "answerable": answerable,
            "rank": _first_relevant_rank(urls, q.get("relevant", [])) if answerable else None,
            "gate_answers": result.answerable(settings.similarity_floor),
            "retrieval_ms": round(retrieval_ms, 1),
            "top_urls": urls[:3],
        }
        if answers:
            started = time.monotonic()
            final, first_token = None, None
            for event in answerer.run(q["question"], []):
                if event["event"] == "delta" and first_token is None:
                    first_token = (time.monotonic() - started) * 1000
                if event["event"] == "final":
                    final = event["data"]
            row["answer_ms"] = round((time.monotonic() - started) * 1000, 1)
            row["first_token_ms"] = round(first_token, 1) if first_token else None
            row["status"] = final["status"] if final else "error"
            text = (final or {}).get("answer") or ""
            row["cited"] = bool((final or {}).get("citations"))
            facts = q.get("must_contain", [])
            row["facts_ok"] = all(f.lower() in text.lower() for f in facts) if facts else None
            row["missing_facts"] = [f for f in facts if f.lower() not in text.lower()]
        rows.append(row)

    answerable_rows = [r for r in rows if r["answerable"]]
    declined_rows = [r for r in rows if not r["answerable"]]
    metrics: dict = {}
    for k in KS:
        hits = [r for r in answerable_rows if r["rank"] and r["rank"] <= k]
        metrics[f"hit_at_{k}"] = len(hits) / len(answerable_rows)
    metrics["mrr"] = statistics.mean(1 / r["rank"] if r["rank"] else 0 for r in answerable_rows)
    metrics["gate_false_refusals"] = sum(not r["gate_answers"] for r in answerable_rows) / len(
        answerable_rows
    )
    metrics["gate_declines_unanswerable"] = (
        sum(not r["gate_answers"] for r in declined_rows) / len(declined_rows)
        if declined_rows
        else None
    )
    lat = [r["retrieval_ms"] for r in rows]
    metrics["retrieval_ms_p50"], metrics["retrieval_ms_p95"] = _pct(lat, 0.5), _pct(lat, 0.95)

    if answers:
        with_facts = [r for r in answerable_rows if r["facts_ok"] is not None]
        metrics["answer_facts"] = (
            sum(r["facts_ok"] for r in with_facts) / len(with_facts) if with_facts else None
        )
        metrics["answer_abstain_rate"] = (
            sum(r["status"] == "abstained" for r in declined_rows) / len(declined_rows)
            if declined_rows
            else None
        )
        answered = [r for r in answerable_rows if r["status"] in ("answered", "partial")]
        metrics["answer_rate"] = len(answered) / len(answerable_rows)
        metrics["grounded_rate"] = (
            sum(r["cited"] for r in answered) / len(answered) if answered else None
        )
        times = [r["answer_ms"] for r in rows]
        first = [r["first_token_ms"] for r in rows if r.get("first_token_ms")]
        metrics["answer_ms_p50"], metrics["answer_ms_p95"] = _pct(times, 0.5), _pct(times, 0.95)
        metrics["first_token_ms_p50"] = _pct(first, 0.5) if first else None

    by_category: dict[str, dict] = {}
    for r in answerable_rows:
        c = by_category.setdefault(r["category"], {"n": 0, "hit5": 0})
        c["n"] += 1
        c["hit5"] += bool(r["rank"] and r["rank"] <= 5)

    checks = []
    for name, limit in thresholds.items():
        value = metrics.get(name)
        if value is None:
            continue
        if name.startswith("answer_") and answerer.llm is None:
            continue  # answer quality thresholds need a real model; extractive mode can't meet them
        ok = value <= limit if name == "gate_false_refusals" else value >= limit
        checks.append({"metric": name, "value": value, "threshold": limit, "ok": ok})

    out_dir = REPO_ROOT / "eval"
    report = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "index_version": version_id,
        "llm": answerer.llm.name if answerer.llm else "extractive",
        "embedding_model": answerer.embedder.model,
        "questions": len(rows),
        "metrics": metrics,
        "by_category": by_category,
        "checks": checks,
        "rows": rows,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    table = Table("Metric", "Value", "Threshold", "")
    for name, value in metrics.items():
        if value is None:
            continue
        check = next((c for c in checks if c["metric"] == name), None)
        shown = f"{value:.0f} ms" if name.endswith(("_ms_p50", "_ms_p95")) else f"{value:.2f}"
        table.add_row(
            name,
            shown,
            f"{check['threshold']}" if check else "",
            ("[green]pass[/green]" if check["ok"] else "[red]FAIL[/red]") if check else "",
        )
    console.print(table)

    cat_table = Table("Category", "Questions", "hit@5")
    for cat, c in sorted(by_category.items()):
        cat_table.add_row(cat, str(c["n"]), f"{c['hit5'] / c['n']:.2f}")
    console.print(cat_table)

    misses = [r for r in answerable_rows if not (r["rank"] and r["rank"] <= 5)]
    if misses:
        console.print("[yellow]No relevant source in the top 5:[/yellow]")
        for r in misses:
            console.print(f"  {r['id']}: top = {r['top_urls'][:1]}")

    md = [
        f"# Ask OLake evaluation ({report['run_at']})",
        "",
        f"LLM: `{report['llm']}` · embeddings: `{report['embedding_model']}` · {len(rows)} questions",
        "",
        "| Metric | Value | Threshold | Result |",
        "| --- | --- | --- | --- |",
    ]
    for name, value in metrics.items():
        if value is None:
            continue
        check = next((c for c in checks if c["metric"] == name), None)
        md.append(
            f"| {name} | {value:.3f} | {check['threshold'] if check else ''} | "
            f"{('pass' if check['ok'] else 'FAIL') if check else ''} |"
        )
    md += ["", "| Category | Questions | hit@5 |", "| --- | --- | --- |"]
    md += [f"| {c} | {v['n']} | {v['hit5'] / v['n']:.2f} |" for c, v in sorted(by_category.items())]
    (out_dir / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    console.print(f"Report written to {out_dir / 'report.md'}")
    passed = all(c["ok"] for c in checks)
    if activate_on_pass:
        if passed:
            with answerer.pool.connection() as conn:
                activate(conn, version_id)
            console.print(f"[green]Index version {version_id} passed and is now active.[/green]")
        else:
            console.print(
                f"[red]Index version {version_id} failed; the active index was not changed.[/red]"
            )
    return passed
