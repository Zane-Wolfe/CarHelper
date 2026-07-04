"""Opt-in sync client: push trip summaries from the device to a configurable
endpoint (the hosted service, or a self-hosted one — pointing at localhost must be
as easy as at ours). Capture/rules/storage never depend on this; sync is additive.
"""
from .client import SyncClient, SyncReport
from .state import SyncState
from .transport import HttpTransport, Response, Transport

__all__ = [
    "SyncClient",
    "SyncReport",
    "SyncState",
    "HttpTransport",
    "Response",
    "Transport",
]
