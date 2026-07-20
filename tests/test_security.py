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
