import os
from collections.abc import Mapping

_SENSITIVE_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")
_MIN_SECRET_LENGTH = 8


def secret_values(env: Mapping[str, str] | None = None) -> list[str]:
    # Values of environment variables whose names look sensitive.
    env = os.environ if env is None else env
    values = {
        value
        for name, value in env.items()
        if len(value) >= _MIN_SECRET_LENGTH
        and any(marker in name.upper() for marker in _SENSITIVE_MARKERS)
    }
    # Longest first so a secret containing another is replaced whole.
    return sorted(values, key=len, reverse=True)


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    return text
