"""Decide product, source type, version, connector and topics for a document.

Rules are deliberately simple and path-first: the URL or file path is the most
reliable signal ("/docs/connectors/postgres/", "drivers/mysql/"). Keyword rules on
titles fill in topics. Every value comes from the closed vocabularies in
olake_shared.taxonomy, so a typo here fails validation instead of inventing a tag.
"""

import re

from olake_shared import Connector, Product, SourceType, Topic

PRODUCT_LABELS = {
    Product.OLAKE_GO: "OLake Go",
    Product.FUSION: "OLake Fusion",
    Product.UI: "OLake UI",
    Product.HELM: "OLake Helm chart",
    Product.EXTERNAL: "External",
}

# Path segments that name a connector, e.g. /docs/connectors/postgres/ or drivers/postgres/.
CONNECTOR_PATH = re.compile(r"(?:^|/)(?:connectors|drivers)/(?P<name>[a-z0-9]+)(?:/|$)")
CONNECTOR_WORDS: dict[Connector, re.Pattern[str]] = {
    Connector.POSTGRES: re.compile(r"\b(postgres(ql)?|pgoutput|wal2json)\b"),
    Connector.MYSQL: re.compile(r"\b(mysql|binlog)\b"),
    Connector.MONGODB: re.compile(r"\b(mongo(db)?|oplog)\b"),
    Connector.ORACLE: re.compile(r"\boracle\b"),
    Connector.MSSQL: re.compile(r"\b(mssql|sql server)\b"),
    Connector.DB2: re.compile(r"\bdb2\b"),
    Connector.KAFKA: re.compile(r"\bkafka\b"),
    # S3 is deliberately path-only: "S3" appears on every destination page too.
}
TOPIC_WORDS: dict[Topic, re.Pattern[str]] = {
    Topic.SETUP: re.compile(r"\b(install|quickstart|getting[- ]started|setup|set up|prerequisite)"),
    Topic.CONFIG: re.compile(r"\b(config|configuration|propert(y|ies)|parameter|setting|flag)"),
    Topic.SYNC_MODE: re.compile(
        r"\b(cdc|incremental|full[- ]refresh|sync[- ]mode|change data capture|strict)"
    ),
    Topic.CATALOG: re.compile(
        r"\b(catalog|glue|nessie|polaris|unity|lakekeeper|hive|jdbc|rest catalog|s3 tables)"
    ),
    Topic.MAINTENANCE: re.compile(r"\b(compaction|maintenance|fusion|expir|snapshot|optimi[sz])"),
    Topic.DEPLOY: re.compile(r"\b(kubernetes|helm|docker|deploy|ec2|airflow|compose)"),
    Topic.TROUBLESHOOT: re.compile(r"\b(troubleshoot|error|faq|issue|debug|fail|not working)"),
}
RELEASE_VERSION = re.compile(r"/release/[^/]+/(?P<v>v[\d.]+(?:-v[\d.]+)?)/?")


def resolve_product(configured: str, path: str) -> Product:
    if configured != "auto":
        return Product(configured)
    return Product.FUSION if path.startswith("/docs/fusion/") else Product.OLAKE_GO


def resolve_source_type(configured: str, path: str) -> SourceType:
    lower = path.lower()
    if configured == "auto":
        return SourceType.RELEASE_NOTE if "/release/" in lower else SourceType.DOC
    kind = SourceType(configured)
    if kind in (SourceType.DOC, SourceType.BLOG):
        return kind
    if lower.endswith(".go"):
        return SourceType.CODE
    if lower.endswith((".yaml", ".yml")):
        return SourceType.CONFIG
    if lower.endswith((".md", ".markdown")):
        return SourceType.README
    return kind


def resolve_version(path: str, git_ref: str | None) -> str:
    if m := RELEASE_VERSION.search(path):
        return m["v"]
    return git_ref or "current"


def detect_connector(path: str, text: str) -> Connector | None:
    if m := CONNECTOR_PATH.search(path.lower()):
        try:
            return Connector(m["name"])
        except ValueError:
            pass  # e.g. drivers/abstract
    found = [c for c, pattern in CONNECTOR_WORDS.items() if pattern.search(text.lower())]
    return found[0] if len(found) == 1 else None


def detect_topics(text: str) -> list[Topic]:
    lower = text.lower()
    return [t for t, pattern in TOPIC_WORDS.items() if pattern.search(lower)]
