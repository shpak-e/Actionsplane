"""Alert routing to Slack / generic webhook / email channels."""

from actionsplane.notifications.service import (
    EVENT_TYPES,
    KINDS,
    dispatch,
    dispatch_once,
    safe_error,
    send_test,
    validate_destination,
)

__all__ = [
    "EVENT_TYPES",
    "KINDS",
    "dispatch",
    "dispatch_once",
    "safe_error",
    "send_test",
    "validate_destination",
]
