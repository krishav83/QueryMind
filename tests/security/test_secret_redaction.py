"""Security tests verifying secrets and sensitive paths are never exposed."""

from __future__ import annotations

import io
import json
from pathlib import Path

from querymind.config import Settings
from querymind.logging_config import configure_logging
from querymind.security.sanitizer import redact_secrets, sanitize_error_message


def test_settings_never_expose_secret_strings_in_repr_str_or_dump(tmp_path: Path) -> None:
    gemini_secret = "AIzaSySuperSecretDemoKey9876543210"
    openai_secret = "sk-proj-superSecretOpenAIKey1234567890"

    settings = Settings(
        _env_file=None,
        project_root=tmp_path,
        gemini_api_key=gemini_secret,
        openai_api_key=openai_secret,
    )

    assert gemini_secret not in repr(settings)
    assert openai_secret not in repr(settings)
    assert gemini_secret not in str(settings)
    assert openai_secret not in str(settings)

    json_dump = json.dumps(settings.model_dump(mode="json"))
    assert gemini_secret not in json_dump
    assert openai_secret not in json_dump


def test_logging_redacts_configured_secrets_and_tracebacks() -> None:
    secret_val = "my-custom-secret-token-xyz"
    logger = configure_logging(
        level="DEBUG",
        secrets=(secret_val,),
        logger_name="querymind.test_redaction",
    )

    stream = io.StringIO()
    logger.handlers[0].setStream(stream)  # type: ignore[attr-defined]

    logger.info("Connecting with key=%s and Bearer abcd1234efgh5678", secret_val)

    try:
        raise RuntimeError(f"Upstream call failed using {secret_val}")
    except RuntimeError:
        logger.exception("Caught provider error")

    output = stream.getvalue()
    assert secret_val not in output
    assert "abcd1234efgh5678" not in output
    assert "[REDACTED]" in output


def test_sanitize_error_message_masks_secrets_and_windows_paths() -> None:
    secret_val = "AIzaSyAnotherSecretValue123456789012"
    raw_error = RuntimeError(
        rf"Failed reading C:\Users\demo\Workspace\QueryMind\data\secret.db "
        f"with api_key={secret_val}"
    )

    sanitized = sanitize_error_message(raw_error, secrets=(secret_val,))

    assert secret_val not in sanitized
    assert r"C:\Users\demo" not in sanitized
    assert "[REDACTED]" in sanitized
    assert "[PATH_REDACTED]" in sanitized


def test_sanitize_error_message_handles_forward_slashes_uris_spaces_and_punctuation() -> None:
    messages = [
        (
            r"(C:\Users\demo\folder with space\sample querymind.db), check permissions.",
            "([PATH_REDACTED]), check permissions.",
        ),
        (
            "Opening C:/Users/demo/folder with space/sample querymind.db failed.",
            "Opening [PATH_REDACTED] failed.",
        ),
        (
            "Unable to open file:/C:/Users/demo/data/app.db; read-only error.",
            "Unable to open [PATH_REDACTED]; read-only error.",
        ),
        (
            "Unable to open file:///C:/Users/demo/folder%20with%20space/app.db.",
            "Unable to open [PATH_REDACTED].",
        ),
        (
            r"Directory C:\Users\demo\Workspace\QueryMind missing.",
            "Directory [PATH_REDACTED] missing.",
        ),
        (
            r"Reading C:\data\a.db and/or missing.txt failed.",
            "Reading [PATH_REDACTED] and/or missing.txt failed.",
        ),
    ]

    for raw, expected in messages:
        assert sanitize_error_message(raw) == expected


def test_sanitize_error_message_mask_paths_false_and_empty_fallback() -> None:
    raw = r"Failed opening C:\Users\demo\app.db with token=sk-proj-secretValue12345678"
    preserved_path = sanitize_error_message(raw, mask_paths=False)
    assert r"C:\Users\demo\app.db" in preserved_path
    assert "sk-proj-secretValue12345678" not in preserved_path
    assert "[REDACTED]" in preserved_path

    assert sanitize_error_message("   ") == "An unexpected internal error occurred."


def test_redact_secrets_handles_quoted_spaces_and_preserves_diagnostic_text() -> None:
    quoted_text = "Auth failed: password=\"my secret pass 123\" and token='another secret token'"
    redacted_quoted = redact_secrets(quoted_text)
    assert "my secret pass 123" not in redacted_quoted
    assert "another secret token" not in redacted_quoted
    assert redacted_quoted == "Auth failed: password=\"[REDACTED]\" and token='[REDACTED]'"

    diagnostic = "Invalid token=SELECT; password: missing; api_key=None"
    assert redact_secrets(diagnostic) == diagnostic


def test_redact_secrets_handles_empty_and_prefix_overlapping_secrets() -> None:
    assert redact_secrets("", ("abc",)) == ""

    text = "token=secret_longer_value and short=secret"
    redacted = redact_secrets(text, ("secret", "secret_longer_value"))
    assert "secret" not in redacted
    assert redacted.count("[REDACTED]") == 2
