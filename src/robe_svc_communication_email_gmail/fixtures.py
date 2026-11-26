from __future__ import annotations

import base64
from typing import Any


def _encoded_body(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


SYNTHETIC_MESSAGES: dict[str, dict[str, Any]] = {
    "gmail-fixture-001": {
        "id": "gmail-fixture-001",
        "threadId": "gmail-thread-fixture-001",
        "internalDate": "1772971200000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Robe Example <example.sender@example.test>"},
                {"name": "Subject", "value": "Synthetic retrieval example"},
                {"name": "Date", "value": "Sun, 08 Mar 2026 12:00:00 +0000"},
            ],
            "body": {"size": 31, "data": _encoded_body("This is a synthetic email body.")},
        },
    }
}


def get_synthetic_message(message_id: str) -> dict[str, Any]:
    try:
        return SYNTHETIC_MESSAGES[message_id]
    except KeyError as error:
        raise ValueError("The requested synthetic message does not exist.") from error
