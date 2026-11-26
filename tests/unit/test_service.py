from __future__ import annotations

import base64
import json
from concurrent.futures import Future
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from robe_svc_communication_email_gmail.app import create_app
from robe_svc_communication_email_gmail.normalization import normalize_gmail_message
from robe_svc_communication_email_gmail.service import (
    EVENT_TYPE,
    EmailRetrievalService,
    build_retrieved_event,
    decode_push_message,
)


class FakePublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes, dict[str, str]]] = []

    def publish(self, topic: str, data: bytes, **attributes: str) -> Future[str]:
        self.messages.append((topic, data, attributes))
        future: Future[str] = Future()
        future.set_result(f"message-{len(self.messages)}")
        return future


def request_event(
    *,
    event_id: str | None = None,
    provider: str = "gmail",
    source_account_id: str = "synthetic-account-001",
    source_message_id: str = "gmail-fixture-001",
    source_thread_id: str | None = "gmail-thread-fixture-001",
    correlation_id: str | None = None,
) -> dict[str, Any]:
    email_identity = {
        "provider": provider,
        "source_account_id": source_account_id,
        "source_message_id": source_message_id,
    }
    if source_thread_id is not None:
        email_identity["source_thread_id"] = source_thread_id
    event = {
        "event_id": event_id or str(uuid4()),
        "event_type": "communication.email.retrieval.requested",
        "contract_version": "1",
        "occurred_at": "2026-03-08T12:00:00Z",
        "source": "synthetic-test-client",
        "payload": {"email_identity": email_identity},
    }
    if correlation_id is not None:
        event["correlation_id"] = correlation_id
    return event


def push_wrapper(event: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(event).encode()
    return {"message": {"data": base64.b64encode(data).decode("ascii")}}


def test_normalization_prefers_plain_text_and_excludes_attachments() -> None:
    message = {
        "id": "fixture",
        "internalDate": "1772971200000",
        "payload": {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": base64.urlsafe_b64encode(b"<p>HTML copy</p>").decode().rstrip("=")},
                },
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"Plain copy").decode().rstrip("=")},
                },
                {
                    "mimeType": "text/plain",
                    "filename": "private.txt",
                    "body": {"data": base64.urlsafe_b64encode(b"Attachment").decode().rstrip("=")},
                },
            ],
        },
    }

    normalized = normalize_gmail_message(message)

    assert normalized["body"] == "Plain copy"
    assert normalized["receivedAt"] == "2026-03-08T12:00:00Z"


def test_normalization_strips_html_active_content_and_reports_external_body() -> None:
    html = b"<p>Hello</p><script>do not include</script>"
    encoded = base64.urlsafe_b64encode(html).decode().rstrip("=")
    normalized = normalize_gmail_message(
        {
            "id": "fixture",
            "payload": {
                "mimeType": "multipart/mixed",
                "parts": [
                    {"mimeType": "text/html", "body": {"data": encoded}},
                    {
                        "mimeType": "text/plain",
                        "body": {"attachmentId": "external", "size": 100},
                    },
                ],
            },
        }
    )

    assert normalized["body"] == "Hello"
    assert "external_body_unavailable" in normalized["warnings"]
    assert "do not include" not in normalized["body"]


def test_redelivered_request_produces_identical_event_content_and_identity() -> None:
    publisher = FakePublisher()
    service = EmailRetrievalService(
        publisher=publisher,
        project_id="robe-local",
        event_topic_id="communication-email-retrieved",
    )
    request = decode_push_message(push_wrapper(request_event(event_id=str(uuid4()))))

    first = service.handle(request)
    second = service.handle(request)

    assert first == second
    assert UUID(first["event_id"])
    assert first["event_type"] == EVENT_TYPE
    assert first["payload"]["email_identity"] == {
        "provider": "gmail",
        "source_account_id": "synthetic-account-001",
        "source_message_id": "gmail-fixture-001",
        "source_thread_id": "gmail-thread-fixture-001",
    }
    assert first["payload"]["body_text"] == "This is a synthetic email body."
    assert "message_id" not in first["payload"]
    assert publisher.messages[0][1] == publisher.messages[1][1]
    assert publisher.messages[0][0] == "projects/robe-local/topics/communication-email-retrieved"


def test_unknown_fixture_is_rejected_without_publishing() -> None:
    publisher = FakePublisher()
    service = EmailRetrievalService(
        publisher=publisher,
        project_id="robe-local",
        event_topic_id="communication-email-retrieved",
    )
    request = decode_push_message(
        push_wrapper(request_event(source_message_id="unknown"))
    )

    try:
        service.handle(request)
    except ValueError as error:
        assert "synthetic message does not exist" in str(error)
    else:
        raise AssertionError("Unknown fixture should be rejected.")

    assert publisher.messages == []


def test_push_route_acknowledges_after_publication_and_rejects_invalid_input() -> None:
    publisher = FakePublisher()
    service = EmailRetrievalService(
        publisher=publisher,
        project_id="robe-local",
        event_topic_id="communication-email-retrieved",
    )
    client = TestClient(create_app(service))
    response = client.post("/pubsub/push", json=push_wrapper(request_event()))
    invalid = client.post("/pubsub/push", json={"message": {"data": "%%%"} })

    assert response.status_code == 204
    assert response.headers["X-Emitted-Event-Id"]
    assert invalid.status_code == 400
    assert len(publisher.messages) == 1


def test_retrieved_event_has_stable_envelope_and_normalized_fields() -> None:
    request = decode_push_message(push_wrapper(request_event(event_id="edc8aa79-26c4-4a14-8f31-99d8e6e5128c")))
    normalized = normalize_gmail_message(
        {
            "id": "gmail-fixture-001",
            "payload": {"headers": [], "mimeType": "text/plain", "body": {"data": ""}},
        }
    )
    event = build_retrieved_event(request, normalized)

    assert event["causation_id"] == request.event_id
    assert event["correlation_id"] == request.event_id
    assert event["occurred_at"] == request.occurred_at
    assert event["source"] == "robe-svc-communication-email-gmail"
    assert event["payload"]["email_identity"] == request.email_identity.as_payload()


def test_same_email_in_different_requests_keeps_business_identity_distinct_from_event_and_workflow() -> None:
    publisher = FakePublisher()
    service = EmailRetrievalService(
        publisher=publisher,
        project_id="robe-local",
        event_topic_id="communication-email-retrieved",
    )
    first_request = decode_push_message(
        push_wrapper(
            request_event(
                event_id="e4f10dd9-6724-445d-8180-4541d77112ae",
                correlation_id="workflow-first",
            )
        )
    )
    second_request = decode_push_message(
        push_wrapper(
            request_event(
                event_id="cb534077-8c37-4096-8a55-799b4e8cc5a8",
                correlation_id="workflow-second",
            )
        )
    )

    first = service.handle(first_request)
    second = service.handle(second_request)

    assert first["payload"]["email_identity"] == second["payload"]["email_identity"]
    assert first["event_id"] != second["event_id"]
    assert first["causation_id"] == first_request.event_id
    assert second["causation_id"] == second_request.event_id
    assert first["correlation_id"] == "workflow-first"
    assert second["correlation_id"] == "workflow-second"


def test_source_thread_identity_is_optional() -> None:
    request = decode_push_message(
        push_wrapper(request_event(source_thread_id=None))
    )
    normalized = normalize_gmail_message(
        {
            "id": "gmail-fixture-001",
            "payload": {"headers": [], "mimeType": "text/plain", "body": {"data": ""}},
        }
    )

    event = build_retrieved_event(request, normalized)

    assert event["payload"]["email_identity"] == {
        "provider": "gmail",
        "source_account_id": "synthetic-account-001",
        "source_message_id": "gmail-fixture-001",
    }


def test_non_gmail_provider_is_rejected_without_publishing() -> None:
    publisher = FakePublisher()
    service = EmailRetrievalService(
        publisher=publisher,
        project_id="robe-local",
        event_topic_id="communication-email-retrieved",
    )
    request = decode_push_message(push_wrapper(request_event(provider="other")))

    try:
        service.handle(request)
    except ValueError as error:
        assert "only the Gmail provider" in str(error)
    else:
        raise AssertionError("An unsupported provider should be rejected.")

    assert publisher.messages == []
