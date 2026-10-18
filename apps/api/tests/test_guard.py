import pytest

from olake_api.guard import REDACTED, RateLimiter, client_hash, scrub


@pytest.mark.parametrize(
    ("text", "label", "secret"),
    [
        ("conn postgres://admin:hunter2@db:5432/app", "password in URL", "hunter2"),
        ('"password": "s3cr3t!"', "credential value", "s3cr3t!"),
        (
            "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCY",
            "credential value",
            "wJalrXUtnFEMI",
        ),
        ("key AKIAIOSFODNN7EXAMPLE here", "AWS access key", "AKIAIOSFODNN7EXAMPLE"),
        ("token sk-ant-api03-abcdefghijklmnopqrstuv", "API key", "sk-ant-api03"),
        (
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456",
            "bearer token",
            "abcdefghijklmnopqrst",
        ),
        (
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
            "private key",
            "MIIEow",
        ),
    ],
)
def test_secrets_are_redacted(text, label, secret):
    result = scrub(text)
    assert secret not in result.text
    assert REDACTED in result.text
    assert label in result.found


def test_ordinary_config_is_untouched():
    text = '{"host": "localhost", "port": 5432, "password": "<password>", "max_threads": 10}'
    result = scrub(text)
    assert result.text == text
    assert result.found == []


def test_rate_limiter_allows_burst_then_blocks():
    limiter = RateLimiter(per_minute=60, burst=3)
    assert [limiter.allow("ip") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("other-ip")  # limits are per client


def test_client_hash_is_stable_and_salted():
    assert client_hash("1.2.3.4", "a") == client_hash("1.2.3.4", "a")
    assert client_hash("1.2.3.4", "a") != client_hash("1.2.3.4", "b")
