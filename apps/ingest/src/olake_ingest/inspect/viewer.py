"""Write out/viewer.html: a self-contained page for checking chunks by eye.

Pick a document on the left; its chunks appear on the right with breadcrumb, tags,
size and a link to the exact citation target, so you can compare with the live page.
"""

import json
from pathlib import Path

from olake_shared import Chunk

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ask OLake: chunk viewer</title>
<style>
:root{--bg:#fafaf8;--panel:#fff;--ink:#1d1d1b;--muted:#6b6a65;--line:#e4e2dc;--accent:#2b59c3;--chip:#eef1f8}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--panel:#1f1f1d;--ink:#ecebe6;--muted:#a3a29b;--line:#34332f;--accent:#8fb0ff;--chip:#2a2f3d}}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--ink)}
header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap}
header h1{font-size:16px;margin:0}header input{flex:1;min-width:200px;padding:6px 10px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--ink)}
.stats{color:var(--muted)}main{display:grid;grid-template-columns:minmax(220px,340px) 1fr;height:calc(100vh - 58px)}
@media (max-width:760px){main{grid-template-columns:1fr;height:auto}#docs{max-height:40vh}}
#docs{overflow:auto;border-right:1px solid var(--line);background:var(--panel)}
#docs button{display:block;width:100%;text-align:left;border:0;border-bottom:1px solid var(--line);background:none;color:var(--ink);padding:8px 12px;cursor:pointer;font:inherit}
#docs button:hover,#docs button.on{background:var(--chip)}#docs small{color:var(--muted);display:block}
#chunks{overflow:auto;padding:16px}.chunk{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin-bottom:14px}
.chunk .head{padding:10px 12px;border-bottom:1px solid var(--line)}.crumb{color:var(--muted);font-size:12px}
.chunk a{color:var(--accent);word-break:break-all}.chip{display:inline-block;background:var(--chip);border-radius:10px;padding:0 8px;margin:4px 4px 0 0;font-size:12px}
pre{margin:0;padding:12px;white-space:pre-wrap;word-break:break-word;font:12.5px/1.5 ui-monospace,monospace}
</style></head><body>
<header><h1>Chunk viewer</h1><input id="q" placeholder="Filter documents by path or title"><span class="stats" id="stats"></span></header>
<main><nav id="docs"></nav><section id="chunks"><p class="stats">Pick a document.</p></section></main>
<script>
const DATA = __DATA__;
const docs = document.getElementById('docs'), list = document.getElementById('chunks');
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
document.getElementById('stats').textContent = DATA.docs.length + ' documents · ' + DATA.total + ' chunks';
function show(i, btn){
  document.querySelectorAll('#docs button').forEach(b => b.classList.remove('on')); btn.classList.add('on');
  list.innerHTML = DATA.docs[i].chunks.map(c => `<article class="chunk"><div class="head">
    <div class="crumb">${esc(c.breadcrumb.join(' › '))}</div>
    <a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.url)}</a><div>
    ${c.tags.map(t => `<span class="chip">${esc(t)}</span>`).join('')}<span class="chip">~${c.tokens} tokens</span></div></div>
    <pre>${esc(c.body)}</pre></article>`).join('');
  list.scrollTop = 0;
}
function render(filter){
  docs.innerHTML = '';
  DATA.docs.forEach((d, i) => {
    if (filter && !(d.path + ' ' + d.title).toLowerCase().includes(filter)) return;
    const b = document.createElement('button');
    b.innerHTML = `${esc(d.title)}<small>${esc(d.source)} · ${esc(d.path)} · ${d.chunks.length}</small>`;
    b.onclick = () => show(i, b); docs.appendChild(b);
  });
}
document.getElementById('q').oninput = e => render(e.target.value.toLowerCase().trim());
render('');
</script></body></html>
"""


def write_viewer(chunks: list[Chunk], out: Path) -> Path:
    by_doc: dict[tuple[str, str], dict] = {}
    for c in chunks:
        key = (c.meta.repo, c.meta.path)
        doc = by_doc.setdefault(
            key,
            {
                "source": c.meta.repo,
                "path": c.meta.path,
                "title": c.meta.title.split(": ")[0],
                "chunks": [],
            },
        )
        tags = [c.meta.product.value, c.meta.source_type.value, f"tier {c.meta.trust_tier}"]
        tags += [c.meta.connector.value] if c.meta.connector else []
        tags += [t.value for t in c.meta.topics]
        doc["chunks"].append(
            {
                "breadcrumb": c.meta.breadcrumb,
                "url": c.meta.url,
                "tags": tags,
                "tokens": len(c.text) // 4,
                "body": c.display_text,
            }
        )
    data = {"docs": list(by_doc.values()), "total": len(chunks)}
    payload = json.dumps(data).replace("</", "<\\/")  # never let content close the <script>
    out.write_text(PAGE.replace("__DATA__", payload), encoding="utf-8")
    return out
