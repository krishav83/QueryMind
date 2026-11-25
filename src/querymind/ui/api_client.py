"""Typed HTTP client used by the Streamlit UI to communicate with FastAPI."""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8001"


def get_api_base_url() -> str:
    """Resolve the backend API URL from ``QUERYMIND_API_URL`` or the default URL."""
    raw = os.environ.get("QUERYMIND_API_URL", "").strip()
    return (raw or DEFAULT_API_URL).rstrip("/")


class APIClientError(Exception):
    """Raised when a backend API call fails or returns an error status."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class QueryMindAPIClient:
    """Thin HTTP wrapper around the QueryMind FastAPI endpoints."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        resolved_url = base_url if base_url is not None else get_api_base_url()
        self._base_url = resolved_url.rstrip("/")
        self._timeout = timeout_seconds
        self._external_client = client

    def health(self) -> dict[str, Any]:
        """Fetch service health and active provider mode."""
        return self._request("GET", "/api/v1/health")

    def get_schema(self) -> dict[str, Any]:
        """Fetch discovered database schema metadata."""
        return self._request("GET", "/api/v1/schema")

    def refresh_schema(self) -> dict[str, Any]:
        """Force backend schema re-inspection."""
        return self._request("POST", "/api/v1/schema/refresh")

    def list_databases(self) -> dict[str, Any]:
        """List uploaded databases managed by the backend."""
        return self._request("GET", "/api/v1/databases")

    def upload_database(self, filename: str, content: bytes) -> dict[str, Any]:
        """Upload a SQLite database file and connect to it."""
        return self._request(
            "POST",
            "/api/v1/databases/upload",
            files={"file": (filename, content, "application/octet-stream")},
        )

    def upload_databases_batch(
        self,
        files: list[tuple[str, bytes]],
        *,
        auto_connect: bool = False,
    ) -> dict[str, Any]:
        """Upload up to 15 SQLite database files in a single batch."""
        multipart = [
            ("files", (filename, content, "application/octet-stream"))
            for filename, content in files
        ]
        return self._request(
            "POST",
            "/api/v1/databases/upload-batch",
            params={"auto_connect": str(auto_connect).lower()},
            files=multipart,
        )

    def select_database(self, database_id: str) -> dict[str, Any]:
        """Switch active connection to a previously uploaded database."""
        return self._request(
            "POST",
            "/api/v1/databases/select",
            json={"database_id": database_id},
        )

    def disconnect_database(self) -> dict[str, Any]:
        """Disconnect the currently connected database."""
        return self._request("POST", "/api/v1/databases/disconnect")

    def ask_query(self, question: str, max_rows: int | None = None) -> dict[str, Any]:
        """Submit a natural-language question for validation and read-only execution."""
        payload: dict[str, Any] = {"question": question}
        if max_rows is not None:
            payload["max_rows"] = max_rows
        return self._request("POST", "/api/v1/query", json=payload)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if self._external_client is not None:
            return self._send(self._external_client, method, path, **kwargs)

        with httpx.Client(base_url=self._base_url, timeout=self._timeout) as client:
            return self._send(client, method, path, **kwargs)

    @staticmethod
    def _send(client: httpx.Client, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise APIClientError(
                "Backend request timed out. Please try a simpler query or check server health."
            ) from exc
        except httpx.HTTPError as exc:
            raise APIClientError(f"Unable to reach QueryMind API backend: {exc}") from exc

        if response.status_code >= 400:
            detail = f"API request failed with HTTP {response.status_code}."
            code: str | None = None
            try:
                err_body = response.json()
                if isinstance(err_body, dict):
                    if err_body.get("detail"):
                        detail = str(err_body["detail"])
                    if err_body.get("code"):
                        code = str(err_body["code"])
            except ValueError:
                pass
            raise APIClientError(detail, status_code=response.status_code, code=code)

        data = response.json()
        if not isinstance(data, dict):
            raise APIClientError("Unexpected response format from QueryMind API.")
        return data
