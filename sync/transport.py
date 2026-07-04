"""HTTP transport for the sync client. Stdlib-only (urllib) to keep the device
footprint minimal, and behind a Protocol so the client is testable without a
network."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Response:
    status: int
    body: str


class Transport(Protocol):
    def post_json(self, url: str, token: str | None, payload: dict) -> Response:
        """POST payload as JSON. Return the Response for any HTTP status.

        Raises for transport-level failures (connection refused, timeout, DNS) so
        the caller can treat them as retryable.
        """
        ...


class HttpTransport:
    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    def post_json(self, url: str, token: str | None, payload: dict) -> Response:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("content-type", "application/json")
        if token:
            req.add_header("authorization", f"Bearer {token}")
        log.debug("POST %s (%d bytes)", url, len(data))
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return Response(status=resp.status, body=resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # An HTTP error status is a real response, not a transport failure.
            body = exc.read().decode("utf-8") if exc.fp is not None else ""
            log.debug("POST %s → HTTP %s", url, exc.code)
            return Response(status=exc.code, body=body)
        # urllib.error.URLError (network) intentionally propagates → retryable.
