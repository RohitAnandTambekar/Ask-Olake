import { describe, expect, it } from "vitest";

import { parseSSE } from "./api";
import { renderAnswer, sourceLabel } from "./markdown";
import type { Source } from "./types";

const source = (n: number, extra: Partial<Source> = {}): Source => ({
  n,
  title: `Source ${n}`,
  url: `https://olake.io/docs/x${n}/`,
  breadcrumb: ["OLake Go", "Sources", "Postgres", "CDC"],
  source_type: "doc",
  product: "olake-go",
  trust_tier: 1,
  ...extra,
});

describe("parseSSE", () => {
  it("returns complete events and keeps the unfinished tail", () => {
    const { events, rest } = parseSSE(
      'event: meta\ndata: {"query_id":"q","notices":[]}\n\nevent: delta\ndata: {"text":"Hel',
    );
    expect(events).toEqual([{ event: "meta", data: { query_id: "q", notices: [] } }]);
    expect(rest).toBe('event: delta\ndata: {"text":"Hel');
  });

  it("skips malformed events instead of throwing", () => {
    expect(parseSSE("event: delta\ndata: {oops\n\n").events).toEqual([]);
  });
});

describe("renderAnswer", () => {
  it("turns [n] into links to the matching source", () => {
    const html = renderAnswer("Create a slot [1][2].", [source(1), source(2)]);
    expect(html).toContain('class="pin"');
    expect(html).toContain('href="https://olake.io/docs/x1/"');
    expect(html).toContain('href="https://olake.io/docs/x2/"');
  });

  it("leaves unknown numbers and code alone", () => {
    const html = renderAnswer("See [7].\n\n```go\nx := a[1]\n```", [source(1)]);
    expect(html).toContain("[7]");
    expect(html).toContain("a[1]");
    expect(html).toContain('data-copy=""');
  });

  it("strips scripts and event handlers from model output", () => {
    const html = renderAnswer('<img src=x onerror="alert(1)"><script>alert(2)</script>[1]', [source(1)]);
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("<script");
  });

  it("opens links in a new tab without opener access", () => {
    expect(renderAnswer("[docs](https://olake.io/docs/)", [])).toContain('rel="noopener noreferrer"');
  });
});

describe("sourceLabel", () => {
  it("shows docs as a breadcrumb path", () => {
    expect(sourceLabel(source(1))).toEqual({ path: "Sources › Postgres › CDC", kind: "Docs" });
  });

  it("shows code as file and line range", () => {
    const code = source(1, {
      source_type: "code",
      url: "https://github.com/datazip-inc/olake/blob/abc/drivers/postgres/internal/config.go#L37-L44",
    });
    expect(sourceLabel(code)).toEqual({ path: "config.go · L37–44", kind: "Code" });
  });
});
