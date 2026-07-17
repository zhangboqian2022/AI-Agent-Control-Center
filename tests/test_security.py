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
