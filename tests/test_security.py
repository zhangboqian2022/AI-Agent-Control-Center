import pytest

from aacc.security import redact


def test_redact_hides_common_secret_formats() -> None:
    value = "token=abc123 password: swordfish Authorization: Bearer very-secret sk-test123456789"
    cleaned = redact(value)
    assert "abc123" not in cleaned
    assert "swordfish" not in cleaned
    assert "very-secret" not in cleaned
    assert "sk-test123456789" not in cleaned
    assert cleaned.count("[REDACTED]") >= 4


def test_redact_leaves_normal_status_text() -> None:
    assert redact("task-1 completed in 12 seconds") == "task-1 completed in 12 seconds"
    assert redact("request failed error_code=500 code=200") == (
        "request failed error_code=500 code=200"
    )


@pytest.mark.parametrize(
    ("value", "secret"),
    [
        ('"device_code": "dev-abc-123456"', "dev-abc-123456"),
        ("user_code=ABCD-EFGH", "ABCD-EFGH"),
        ('"user_code": "WXYZ-1234"', "WXYZ-1234"),
        ("api_key=plainkey-no-sk-prefix", "plainkey-no-sk-prefix"),
        ('"apikey": "another-key-value"', "another-key-value"),
        ("redirect_uri=https://example.com/cb?device_code=dev-777", "dev-777"),
    ],
)
def test_redacts_oauth_and_api_key_fields(value: str, secret: str) -> None:
    cleaned = redact(value)
    assert "[REDACTED]" in cleaned
    assert secret not in cleaned


@pytest.mark.parametrize(
    "value",
    [
        '"token": "abc123456"',
        "password='hunter2'",
        "secret: super-secret",
        '"authorization": "Bearer abc.def"',
    ],
)
def test_redacts_quoted_and_structured_secret_values(value: str) -> None:
    cleaned = redact(value)
    assert "[REDACTED]" in cleaned
    secrets = ("abc123456", "hunter2", "super-secret", "abc.def")
    assert all(secret not in cleaned for secret in secrets)
