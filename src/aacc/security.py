import re

SECRET_PATTERNS = (
    re.compile(
        r"(?i)([\"']?(?:token|password|secret)[\"']?\s*[=:]\s*)([\"']?)"
        r"([^\"'\s,;}\]]+)([\"']?)"
    ),
    re.compile(
        r"(?i)([\"']?authorization[\"']?\s*[=:]\s*[\"']?bearer\s+)"
        r"[^\"'\s,;}\]]+"
    ),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def redact(value: str) -> str:
    cleaned = value
    for pattern in SECRET_PATTERNS:
        if pattern.groups == 4:
            cleaned = pattern.sub(
                lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(4)}",
                cleaned,
            )
        elif pattern.groups:
            cleaned = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", cleaned)
        else:
            cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned
