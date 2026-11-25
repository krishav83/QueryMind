"""Secret redaction and client-safe error sanitization helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable

_REDACTED = "[REDACTED]"
_PATH_REDACTED = "[PATH_REDACTED]"

_DIAGNOSTIC_NON_SECRETS: frozenset[str] = frozenset(
    {
        "select",
        "insert",
        "update",
        "delete",
        "invalid",
        "missing",
        "required",
        "empty",
        "none",
        "null",
        "true",
        "false",
        "unset",
        "expired",
        "unknown",
        "error",
        "failed",
        "[redacted]",
    }
)

# Fallback patterns for known credential formats and explicit key/value secrets.
_RAW_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
)
_BEARER_PATTERN = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-+/=]{8,}")
_QUOTED_KV_SECRET_PATTERN = re.compile(
    r"""(?i)\b(api[_-]?key|secret|token|password)\s*([:=])\s*(['"])([^'"\r\n]+)\3"""
)
_UNQUOTED_KV_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password)\s*([:=])\s*([^\s'\",;)\]]+)"
)

# Windows paths (backslash or forward slash, including spaces before extension) and file: URIs.
_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"file:/{1,3}(?:localhost/)?"
        r"(?:[A-Za-z]:\\(?:[^\\/:*?\"<>|'\r\n]+\\)*[^\\/:*?\"<>|'\r\n]*?\.[A-Za-z0-9]{1,8}"
        r"(?=[\s'\"<>),;:\]]|$|\.(?:\s|$))"
        r"|[A-Za-z]:/(?:[^\\/:*?\"<>|'\r\n]+/)*[^\\/:*?\"<>|'\r\n]*?\.[A-Za-z0-9]{1,8}"
        r"(?=[\s'\"<>),;:\]]|$|\.(?:\s|$))"
        r"|[^\s'\"<>),;\]]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"[A-Za-z]:\\(?:[^\\/:*?\"<>|'\r\n]+\\)*[^\\/:*?\"<>|'\r\n]*?\.[A-Za-z0-9]{1,8}"
        r"(?=[\s'\"<>),;:\]]|$|\.(?:\s|$))"
    ),
    re.compile(
        r"[A-Za-z]:/(?:[^\\/:*?\"<>|'\r\n]+/)*[^\\/:*?\"<>|'\r\n]*?\.[A-Za-z0-9]{1,8}"
        r"(?=[\s'\"<>),;:\]]|$|\.(?:\s|$))"
    ),
    re.compile(
        r"[A-Za-z]:\\(?:[^\\/:*?\"<>|'\r\n]+\\)*[^\s\\/:*?\"<>|'\".;,)\]]+"
        r"(?:\.[^\s\\/:*?\"<>|'\".;,)\]]+)*"
    ),
    re.compile(
        r"[A-Za-z]:/(?:[^\\/:*?\"<>|'\r\n]+/)*[^\s\\/:*?\"<>|'\".;,)\]]+"
        r"(?:\.[^\s\\/:*?\"<>|'\".;,)\]]+)*"
    ),
)


def _looks_like_secret_value(value: str) -> bool:
    """Return True if an unquoted credential-field value appears to be an actual secret."""
    lowered = value.strip().lower()
    if not lowered or lowered in _DIAGNOSTIC_NON_SECRETS:
        return False
    has_non_alpha = any(not ch.isalpha() for ch in value)
    has_mixed_case = any(ch.islower() for ch in value) and any(ch.isupper() for ch in value)
    return has_non_alpha or (has_mixed_case and len(value) >= 8) or len(value) >= 16


def redact_secrets(text: str, secrets: Iterable[str] = ()) -> str:
    """Mask known secret values and common credential patterns inside ``text``."""
    if not text:
        return text

    sanitized = text
    # Sort longest-first so a shorter prefix cannot partially mask a longer secret.
    unique_secrets = sorted(
        {s for s in secrets if isinstance(s, str) and len(s.strip()) >= 3},
        key=len,
        reverse=True,
    )
    for secret in unique_secrets:
        sanitized = sanitized.replace(secret, _REDACTED)

    for pattern in _RAW_TOKEN_PATTERNS:
        sanitized = pattern.sub(_REDACTED, sanitized)

    sanitized = _BEARER_PATTERN.sub(rf"\1 {_REDACTED}", sanitized)
    sanitized = _QUOTED_KV_SECRET_PATTERN.sub(
        lambda m: (
            f"{m.group(1)}{m.group(2)}{m.group(3)}{_REDACTED}{m.group(3)}"
            if m.group(4).strip().lower() not in _DIAGNOSTIC_NON_SECRETS
            else m.group(0)
        ),
        sanitized,
    )
    sanitized = _UNQUOTED_KV_SECRET_PATTERN.sub(
        lambda m: (
            f"{m.group(1)}{m.group(2)}{_REDACTED}"
            if _looks_like_secret_value(m.group(3))
            else m.group(0)
        ),
        sanitized,
    )

    return sanitized


def sanitize_error_message(
    error: Exception | str,
    secrets: Iterable[str] = (),
    *,
    mask_paths: bool = True,
) -> str:
    """Produce a client-safe error message with secrets and optional local paths masked."""
    raw = str(error)
    sanitized = redact_secrets(raw, secrets)
    if mask_paths:
        for pattern in _PATH_PATTERNS:
            sanitized = pattern.sub(_PATH_REDACTED, sanitized)
    return sanitized.strip() or "An unexpected internal error occurred."
