"""Unit tests for QueryService orchestration and validation-before-execution gate."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from querymind.db.executor import QueryExecutor
from querymind.db.inspector import SchemaInspector
from querymind.domain.exceptions import UnsafeSQLError
from querymind.domain.models import GeneratedSQL, QueryRequest
from querymind.explainer.explainer import SQLExplainer
from querymind.llm.providers.fallback import DeterministicFallbackProvider
from querymind.security.validator import SQLValidator
from querymind.services.query_service import QueryService


def test_query_service_never_executes_when_validation_fails(seeded_db_path) -> None:
    inspector = SchemaInspector(seeded_db_path)
    malicious_provider = MagicMock()
    malicious_provider.mode = "llm"
    malicious_provider.name = "mock_llm"
    malicious_provider.generate_sql.return_value = GeneratedSQL(
        sql="DROP TABLE orders",
        params={},
        provider_name="mock_llm",
        mode="llm",
    )

    mock_executor = MagicMock(spec=QueryExecutor)
    service = QueryService(
        inspector=inspector,
        provider=malicious_provider,
        validator=SQLValidator(),
        executor=mock_executor,
        explainer=SQLExplainer(),
    )

    with pytest.raises(UnsafeSQLError):
        service.ask(QueryRequest(question="Drop orders"))

    mock_executor.execute.assert_not_called()


def test_query_service_executes_fallback_query_end_to_end(seeded_db_path) -> None:
    inspector = SchemaInspector(seeded_db_path)
    service = QueryService(
        inspector=inspector,
        provider=DeterministicFallbackProvider(),
        validator=SQLValidator(default_max_rows=100),
        executor=QueryExecutor(seeded_db_path, max_rows=100, timeout_seconds=5.0),
        explainer=SQLExplainer(),
    )

    response = service.ask(QueryRequest(question="Count orders by status"))
    assert response.provider_mode == "fallback"
    assert response.row_count == 3
    assert "Groups results" in response.explanation
