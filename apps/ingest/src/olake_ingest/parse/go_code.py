"""Split Go source into one section per top-level declaration and extract symbols.

Symbols are exact-match handles the API uses before semantic search:
- config_key:   json tags on struct fields (`json:"max_threads"`), i.e. config file keys
- error_string: literal messages passed to errors.New / fmt.Errorf / logger error calls
- env_var:      names read with os.Getenv / os.LookupEnv
- struct, func: declared names
"""

import re

import tree_sitter_go
from tree_sitter import Language, Node, Parser

from olake_ingest.parse.model import ParsedDocument, ParsedSymbol, Section
from olake_shared import SymbolKind

_parser = Parser(Language(tree_sitter_go.language()))

MAX_LINES = 90  # longer declarations are split into windows
OVERLAP = 10
JSON_TAG = re.compile(r'json:"([^",]+)')
ERROR_CALLS = re.compile(r"(^|\.)(New|Errorf|Wrap|Wrapf|Fatal|Fatalf|Error|Panicf?)$")
ENV_CALLS = re.compile(r"(^|\.)(Getenv|LookupEnv)$")
MIN_ERROR_LEN = 8


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _decl_name(node: Node, src: bytes) -> str:
    if node.type in ("function_declaration", "method_declaration"):
        name = node.child_by_field_name("name")
        receiver = node.child_by_field_name("receiver")
        base = _text(name, src) if name else "func"
        if receiver is not None:
            rtype = re.sub(r"[()*\s]", " ", _text(receiver, src)).split()
            return f"{rtype[-1]}.{base}" if rtype else base
        return base
    names = [
        _text(n, src)
        for spec in node.named_children
        for n in [spec.child_by_field_name("name")]
        if n is not None
    ]
    if names:
        return ", ".join(names[:3]) + (" …" if len(names) > 3 else "")
    first = next((c for c in node.named_children), None)
    ident = first and next((c for c in first.named_children if c.type == "identifier"), None)
    return _text(ident, src) if ident else node.type.replace("_declaration", "")


def _string_value(node: Node, src: bytes) -> str | None:
    if node.type == "interpreted_string_literal":
        return _text(node, src)[1:-1]
    if node.type == "raw_string_literal":
        return _text(node, src)[1:-1]
    return None


def _symbols(root: Node, src: bytes, locate) -> list[ParsedSymbol]:
    out: list[ParsedSymbol] = []
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        line = node.start_point[0] + 1
        if node.type == "field_declaration":
            tag = node.child_by_field_name("tag")
            if tag is not None:
                for key in JSON_TAG.findall(_text(tag, src)):
                    if key != "-":
                        out.append(ParsedSymbol(key, SymbolKind.CONFIG_KEY, line, locate(line)))
        elif node.type == "call_expression":
            fn = node.child_by_field_name("function")
            args = node.child_by_field_name("arguments")
            if fn is None or args is None or not args.named_children:
                continue
            fn_name = _text(fn, src)
            value = _string_value(args.named_children[0], src)
            if value is None:
                continue
            if ERROR_CALLS.search(fn_name) and len(value) >= MIN_ERROR_LEN:
                out.append(ParsedSymbol(value, SymbolKind.ERROR_STRING, line, locate(line)))
            elif ENV_CALLS.search(fn_name):
                out.append(ParsedSymbol(value, SymbolKind.ENV_VAR, line, locate(line)))
    return out


def parse_go(text: str, path: str) -> ParsedDocument:
    src = text.encode("utf-8")
    tree = _parser.parse(src)
    root = tree.root_node
    lines = text.splitlines()

    package = next(
        (
            _text(c, src).removeprefix("package ").strip()
            for c in root.children
            if c.type == "package_clause"
        ),
        "",
    )
    title = f"{path} (package {package})" if package else path

    sections: list[Section] = []
    symbols: list[ParsedSymbol] = []
    pending_comment_start: int | None = None
    seen: dict[str, int] = {}

    for node in root.children:
        if node.type == "comment":
            if pending_comment_start is None:
                pending_comment_start = node.start_point[0]
            continue
        if node.type in ("package_clause", "import_declaration") or not node.is_named:
            pending_comment_start = None
            continue

        start = pending_comment_start if pending_comment_start is not None else node.start_point[0]
        end = node.end_point[0]  # inclusive, 0-based
        pending_comment_start = None
        name = _decl_name(node, src)
        kind = {
            "function_declaration": SymbolKind.FUNC,
            "method_declaration": SymbolKind.FUNC,
            "type_declaration": SymbolKind.STRUCT,
        }.get(node.type)
        if kind and "," not in name:
            symbols.append(ParsedSymbol(name, kind, node.start_point[0] + 1, ""))

        windows = [(start, end)]
        if end - start + 1 > MAX_LINES:
            windows = []
            s = start
            while s <= end:
                e = min(s + MAX_LINES - 1, end)
                windows.append((s, e))
                if e == end:
                    break
                s = e + 1 - OVERLAP
        for part, (s, e) in enumerate(windows, start=1):
            base = name if len(windows) == 1 else f"{name} (part {part}/{len(windows)})"
            count = seen.get(base, 0)
            seen[base] = count + 1
            locator = base if count == 0 else f"{base}#{count + 1}"
            code = "\n".join(lines[s : e + 1])
            sections.append(
                Section(
                    title=base,
                    locator=locator,
                    heading_path=[title, base],
                    body=f"```go\n{code}\n```",
                    line_start=s + 1,
                    line_end=e + 1,
                )
            )

    def locate(line: int) -> str:
        for sec in sections:
            if sec.line_start <= line <= sec.line_end:
                return sec.locator
        return ""

    for sym in symbols:
        sym.locator = locate(sym.line)
    symbols.extend(_symbols(root, src, locate))
    return ParsedDocument(path=path, title=title, breadcrumb=[], sections=sections, symbols=symbols)
