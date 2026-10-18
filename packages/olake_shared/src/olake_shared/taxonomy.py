"""Controlled vocabularies for chunk metadata.

Every value here becomes a filter the retriever can use later, so keep the
lists closed: adding a new value is a deliberate change, not a typo that
silently creates a new category.
"""

from enum import StrEnum


class Product(StrEnum):
    OLAKE_GO = "olake-go"  # ingestion engine (datazip-inc/olake)
    FUSION = "fusion"  # Iceberg table maintenance (datazip-inc/olake-fusion)
    UI = "ui"  # web UI + BFF (datazip-inc/olake-ui)
    HELM = "helm"  # Kubernetes charts (datazip-inc/olake-helm)
    EXTERNAL = "external"  # allowlisted outside sources (Iceberg spec, catalog docs)


class SourceType(StrEnum):
    DOC = "doc"
    RELEASE_NOTE = "release-note"
    BLOG = "blog"
    CODE = "code"
    CONFIG = "config"
    README = "readme"
    SPEC = "spec"


class Connector(StrEnum):
    POSTGRES = "postgres"
    MYSQL = "mysql"
    MONGODB = "mongodb"
    ORACLE = "oracle"
    MSSQL = "mssql"
    DB2 = "db2"
    KAFKA = "kafka"
    S3 = "s3"


class Topic(StrEnum):
    SETUP = "setup"
    CONFIG = "config"
    SYNC_MODE = "sync-mode"
    CATALOG = "catalog"
    MAINTENANCE = "maintenance"
    DEPLOY = "deploy"
    TROUBLESHOOT = "troubleshoot"


class SymbolKind(StrEnum):
    CONFIG_KEY = "config_key"
    FLAG = "flag"
    ENV_VAR = "env_var"
    ERROR_STRING = "error_string"
    STRUCT = "struct"
    FUNC = "func"
