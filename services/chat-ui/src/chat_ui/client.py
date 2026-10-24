"""Synchronous client for the orchestrator API (Streamlit scripts run synchronously)."""

from collections.abc import Iterator
from typing import Any

import httpx

from poc_common.events import Event
from poc_common.sse import parse_sse_lines


class OrchestratorError(RuntimeError):
    """The orchestrator could not be reached or rejected the request."""


class OrchestratorClient:
    def __init__(self, base_url: str, *, http: httpx.Client | None = None) -> None:
        # CPU-only local models can take minutes per turn; the read timeout is generous.
        self._http = http or httpx.Client(
            base_url=base_url.rstrip("/"), timeout=httpx.Timeout(10.0, read=900.0)
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise OrchestratorError(f"cannot reach the orchestrator ({exc})") from exc
        if response.status_code >= 400:
            raise OrchestratorError(_detail(response))
        return response.json()

    def list_customers(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/demo/customers")

    def reset_customer(self, customer_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/demo/customers/{customer_id}/reset")

    def create_session(self, customer_id: str, expert_name: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST", "/v1/sessions", json={"customer_id": customer_id, "expert_name": expert_name}
        )

    def list_sessions(self, customer_id: str) -> list[dict[str, Any]]:
        """The customer's earlier cases, most recently active first."""
        return self._request("GET", "/v1/sessions", params={"customer_id": customer_id})

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/sessions/{session_id}")

    def stream_turn(self, session_id: str, message: str) -> Iterator[Event]:
        """Yield the turn's events as soon as each one arrives."""
        return self._stream(f"/v1/sessions/{session_id}/messages", {"message": message})

    def stream_consent(
        self, session_id: str, consent_id: str, approved: bool, comment: str | None = None
    ) -> Iterator[Event]:
        """Send the expert's decision on the pending approval and stream the agent's follow-up."""
        body = {"consent_id": consent_id, "approved": approved, "comment": comment}
        return self._stream(f"/v1/sessions/{session_id}/consent", body)

    def _stream(self, path: str, body: dict[str, Any]) -> Iterator[Event]:
        try:
            with self._http.stream("POST", path, json=body) as response:
                if response.status_code >= 400:
                    response.read()
                    raise OrchestratorError(_detail(response))
                yield from parse_sse_lines(response.iter_lines())
        except httpx.HTTPError as exc:
            raise OrchestratorError(f"the connection to the orchestrator broke ({exc})") from exc


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    return f"HTTP {response.status_code}: {detail}"
