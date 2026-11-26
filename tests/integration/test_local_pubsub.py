from __future__ import annotations

import json
import os
import time
from uuid import uuid4

import httpx
import pytest
from google.cloud import pubsub_v1


@pytest.mark.integration
def test_synthetic_request_is_pushed_and_published_with_stable_identity() -> None:
    project_id = os.environ.get("PUBSUB_PROJECT_ID", "robe-local")
    emulator_host = os.environ.get("PUBSUB_EMULATOR_HOST", "127.0.0.1:8085")
    request_topic_id = os.environ.get("REQUEST_TOPIC_ID", "communication-email-retrieval-requests")
    event_topic_id = os.environ.get("EVENT_TOPIC_ID", "communication-email-retrieved")
    service_url = os.environ.get("EMAIL_GMAIL_SERVICE_URL", "http://127.0.0.1:18080")
    os.environ["PUBSUB_EMULATOR_HOST"] = emulator_host

    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()
    event_topic = publisher.topic_path(project_id, event_topic_id)
    request_topic = publisher.topic_path(project_id, request_topic_id)
    test_subscription = subscriber.subscription_path(
        project_id, f"email-retrieved-test-{uuid4().hex}"
    )
    subscription_created = False

    def receive_event(timeout: float = 30) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = subscriber.pull(
                request={"subscription": test_subscription, "max_messages": 1}
            )
            if response.received_messages:
                received = response.received_messages[0]
                subscriber.acknowledge(
                    request={
                        "subscription": test_subscription,
                        "ack_ids": [received.ack_id],
                    }
                )
                return json.loads(received.message.data)
            time.sleep(0.2)
        raise AssertionError("Timed out waiting for the synthetic email event.")

    try:
        subscriber.create_subscription(
            request={"name": test_subscription, "topic": event_topic, "ack_deadline_seconds": 30}
        )
        subscription_created = True
        request_id = str(uuid4())
        request = {
            "event_id": request_id,
            "event_type": "communication.email.retrieval.requested",
            "contract_version": "1",
            "occurred_at": "2026-03-08T12:00:00Z",
            "source": "synthetic-integration-test",
            "payload": {
                "email_identity": {
                    "provider": "gmail",
                    "source_account_id": "synthetic-account-001",
                    "source_message_id": "gmail-fixture-001",
                    "source_thread_id": "gmail-thread-fixture-001",
                }
            },
        }
        health = httpx.get(f"{service_url}/healthz", timeout=5)
        health.raise_for_status()
        publisher.publish(request_topic, json.dumps(request).encode()).result(timeout=10)
        first = receive_event()
        publisher.publish(request_topic, json.dumps(request).encode()).result(timeout=10)
        second = receive_event()
        assert first == second
        assert first["event_type"] == "communication.email.retrieved"
        assert first["causation_id"] == request_id
        assert first["payload"]["email_identity"]["source_thread_id"] == "gmail-thread-fixture-001"
        assert first["payload"]["sender"]["address"] == "example.sender@example.test"
        assert first["payload"]["body_text"] == "This is a synthetic email body."
    finally:
        if subscription_created:
            subscriber.delete_subscription(request={"subscription": test_subscription})
        publisher.transport.close()
        subscriber.transport.close()
