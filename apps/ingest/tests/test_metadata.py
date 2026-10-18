import pytest

from olake_ingest.enrich import metadata as md
from olake_shared import Connector, Product, SourceType, Topic


@pytest.mark.parametrize(
    ("configured", "path", "expected"),
    [
        ("auto", "/docs/fusion/compaction/configuration/", Product.FUSION),
        ("auto", "/docs/connectors/postgres/", Product.OLAKE_GO),
        ("helm", "helm/olake/values.yaml", Product.HELM),
    ],
)
def test_product(configured, path, expected):
    assert md.resolve_product(configured, path) is expected


@pytest.mark.parametrize(
    ("configured", "path", "expected"),
    [
        ("auto", "/docs/release/ingestion/v0.12.0/", SourceType.RELEASE_NOTE),
        ("auto", "/docs/connectors/postgres/", SourceType.DOC),
        ("code", "drivers/postgres/internal/config.go", SourceType.CODE),
        ("code", "README.md", SourceType.README),
        ("config", "helm/olake/values.yaml", SourceType.CONFIG),
        ("doc", "docs/admin-guides/deployment.md", SourceType.DOC),
    ],
)
def test_source_type(configured, path, expected):
    assert md.resolve_source_type(configured, path) is expected


def test_version_from_release_path_or_tag():
    assert md.resolve_version("/docs/release/ingestion/v0.12.0/", None) == "v0.12.0"
    assert md.resolve_version("drivers/x.go", "v0.12.1") == "v0.12.1"
    assert md.resolve_version("/docs/install/", None) == "current"


def test_connector_path_beats_text_and_s3_is_path_only():
    assert md.detect_connector("/docs/connectors/mysql/", "postgres mentioned") is Connector.MYSQL
    assert md.detect_connector("drivers/s3/internal/x.go", "") is Connector.S3
    assert md.detect_connector("/docs/writers/iceberg/", "write Iceberg to S3") is None
    assert md.detect_connector("/blog/x/", "MySQL binlog CDC") is Connector.MYSQL
    assert md.detect_connector("/blog/x/", "MySQL vs Postgres") is None  # ambiguous


def test_topics():
    topics = md.detect_topics("Troubleshooting Postgres CDC configuration on Kubernetes")
    assert {Topic.TROUBLESHOOT, Topic.SYNC_MODE, Topic.CONFIG, Topic.DEPLOY} <= set(topics)
