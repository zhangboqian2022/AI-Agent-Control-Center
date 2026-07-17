import re

SECRET_PATTERNS = (
    re.compile(r"(?i)(token\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(password\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def redact(value: str) -> str:
    cleaned = value
    for pattern in SECRET_PATTERNS:
        if pattern.groups:
            cleaned = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", cleaned)
        else:
            cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned
