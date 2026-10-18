"""Check a generated answer against the sources it was given.

Deterministic and cheap, so it runs on every answer:
1. Citations: [n] markers must refer to a provided source; invalid ones are removed.
   An answer with no valid citation at all is not shown as an answer.
2. Identifiers: names in backticks (`max_threads`) and keys in JSON/YAML code blocks
   must appear somewhere in the sources. Unknown ones are reported as warnings,
   which catches invented config keys and flags.
3. Links: only links to the provided sources or to OLake's own sites are kept.

The offline evaluation adds an LLM judge for "is each sentence supported"; that is
too slow and costly for every live request.
"""

import re
from dataclasses import dataclass, field

CITATION_GROUP = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
BACKTICK = re.compile(r"(?<!`)`([^`\n]{2,80})`(?!`)")
CODE_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
JSON_KEY = re.compile(r'"([A-Za-z_][A-Za-z0-9_.\-]{1,60})"\s*:')
YAML_KEY = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.\-]{1,60}):(\s|$)", re.MULTILINE)
MD_LINK = re.compile(r"\[([^\]\n]{1,200})\]\((https?://[^)\s]+)\)")
BARE_URL = re.compile(r"(?<![(<\w])https?://[^\s)>\]]+")
IDENTIFIER_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/:]*$")
TRUSTED_PREFIXES = ("https://olake.io/", "https://github.com/datazip-inc/")


@dataclass
class Verified:
    text: str
    citations: list[int]  # valid source numbers actually cited, in first-use order
    status: str  # "answered" | "partial" | "ungrounded"
    warnings: list[str] = field(default_factory=list)


def _is_identifier(token: str) -> bool:
    # snake_case, dotted, dashed or path-like names; plain words are not checked
    return bool(IDENTIFIER_SHAPE.match(token)) and any(c in token for c in "_.-/")


def verify(answer: str, sources: list[str], source_urls: list[str]) -> Verified:
    n_sources = len(sources)
    cited: list[int] = []

    def fix_citation(m: re.Match) -> str:
        numbers = [int(x) for x in re.split(r"\s*,\s*", m.group(1))]
        valid = [n for n in numbers if 1 <= n <= n_sources]
        for n in valid:
            if n not in cited:
                cited.append(n)
        return "".join(f"[{n}]" for n in valid)

    # don't touch [n] inside code blocks (array indexes in examples)
    parts = re.split(r"(```.*?```)", answer, flags=re.DOTALL)
    text = "".join(p if p.startswith("```") else CITATION_GROUP.sub(fix_citation, p) for p in parts)

    corpus = "\n".join(sources).lower()
    unknown: list[str] = []
    for token in BACKTICK.findall(text):
        token = token.strip()
        if _is_identifier(token) and token.lower() not in corpus and token not in unknown:
            unknown.append(token)
    for block in CODE_BLOCK.findall(text):
        for key in JSON_KEY.findall(block) + [k for k, _ in YAML_KEY.findall(block)]:
            if key.lower() not in corpus and key not in unknown:
                unknown.append(key)

    allowed = set(source_urls)

    def keep_link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        return m.group(0) if url in allowed or url.startswith(TRUSTED_PREFIXES) else label

    text = MD_LINK.sub(keep_link, text)
    text = BARE_URL.sub(
        lambda m: (
            m.group(0)
            if m.group(0) in allowed or m.group(0).startswith(TRUSTED_PREFIXES)
            else "(link removed)"
        ),
        text,
    )

    warnings: list[str] = []
    if unknown:
        warnings.append(
            "These names do not appear in the sources, so double-check them: "
            + ", ".join(f"`{u}`" for u in unknown[:8])
        )
    if not cited:
        status = "ungrounded"
    elif warnings:
        status = "partial"
    else:
        status = "answered"
    return Verified(text=text.strip(), citations=cited, status=status, warnings=warnings)
