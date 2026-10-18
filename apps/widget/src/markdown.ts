import DOMPurify from "dompurify";
import { marked } from "marked";

import type { Source } from "./types";

marked.setOptions({ gfm: true, breaks: false });

let hooked = false;
function ensureHooks() {
  if (hooked) return;
  hooked = true;
  // every link opens in a new tab and can't reach back into this page
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A") {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
  });
}

const CITATION = /\[(\d+)\]/g;

/**
 * Markdown → sanitized HTML, with [n] markers turned into links to source n.
 * Citations inside code, pre and existing links are left alone.
 */
export function renderAnswer(markdown: string, sources: Source[]): string {
  ensureHooks();
  const html = DOMPurify.sanitize(marked.parse(markdown, { async: false }) as string, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["style", "form", "input", "img"],
  });
  const template = document.createElement("template");
  template.innerHTML = html;

  const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_TEXT);
  const textNodes: Text[] = [];
  while (walker.nextNode()) {
    const node = walker.currentNode as Text;
    if (!node.parentElement?.closest("code, pre, a") && CITATION.test(node.data)) textNodes.push(node);
    CITATION.lastIndex = 0;
  }
  for (const node of textNodes) {
    const fragment = document.createDocumentFragment();
    let last = 0;
    for (const m of node.data.matchAll(CITATION)) {
      const n = Number(m[1]);
      const source = sources[n - 1];
      fragment.append(node.data.slice(last, m.index));
      if (source) {
        const a = document.createElement("a");
        a.className = "pin";
        a.href = source.url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = String(n);
        a.setAttribute("aria-label", `Source ${n}: ${source.title}`);
        a.title = source.title;
        fragment.append(a);
      } else {
        fragment.append(m[0]);
      }
      last = (m.index ?? 0) + m[0].length;
    }
    fragment.append(node.data.slice(last));
    node.replaceWith(fragment);
  }

  // copy buttons on code blocks (handled by event delegation in the component)
  template.content.querySelectorAll("pre").forEach((pre) => {
    const wrap = document.createElement("div");
    wrap.className = "code";
    pre.replaceWith(wrap);
    wrap.append(pre);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "copy";
    button.dataset.copy = "";
    button.textContent = "Copy";
    wrap.append(button);
  });
  return template.innerHTML;
}

/** "Postgres › CDC Prerequisite" for docs; "config.go · L37–44" for code. */
export function sourceLabel(source: Source): { path: string; kind: string } {
  const kind =
    source.source_type === "code" || source.source_type === "config"
      ? "Code"
      : source.source_type === "blog"
        ? "Blog"
        : source.source_type === "release-note"
          ? "Release notes"
          : source.source_type === "readme"
            ? "README"
            : "Docs";
  if (kind === "Code" || kind === "README") {
    const file = source.url.split("?")[0].split("#")[0].split("/").pop() ?? source.title;
    const lines = source.url.match(/#L(\d+)(?:-L(\d+))?$/);
    const range = lines ? ` · L${lines[1]}${lines[2] ? `–${lines[2]}` : ""}` : "";
    return { path: `${file}${range}`, kind };
  }
  const crumbs = source.breadcrumb.filter((c) => !c.startsWith("["));
  return { path: crumbs.slice(1).join(" › ") || source.title, kind };
}
