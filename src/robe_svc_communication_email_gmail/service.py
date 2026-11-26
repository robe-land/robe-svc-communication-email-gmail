from __future__ import annotations

import base64
import binascii
import json
import logging
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from google.cloud import pubsub_v1

from robe_svc_communication_email_gmail.config import Settings
from robe_svc_communication_email_gmail.fixtures import get_synthetic_message
from robe_svc_communication_email_gmail.normalization import normalize_gmail_message

LOGGER = logging.getLogger(__name__)
EVENT_TYPE = "communication.email.retrieved"
REQUEST_TYPE = "communication.email.retrieval.requested"
CONTRACT_VERSION = "1"
SOURCE = "robe-svc-communication-email-gmail"
EVENT_NAMESPACE = uuid5(NAMESPACE_URL, "https://robe.land/robe-svc-communication-email-gmail/event/v1")


class InvalidMessage(ValueError):
    """A message that cannot be handled as a valid retrieval request."""


class PublishClient(Protocol):
    def publish(self, topic: str, data: bytes, **attributes: str) -> Future[str]: ...


@dataclass(frozen=True)
class EmailIdentity:
    provider: str
    source_account_id: str
    source_message_id: str
    source_thread_id: str | None

    def as_payload(self) -> dict[str, str]:
        identity = {
            "provider": self.provider,
            "source_account_id": self.source_account_id,
            "source_message_id": self.source_message_id,
        }
        if self.source_thread_id is not None:
            identity["source_thread_id"] = self.source_thread_id
        return identity


@dataclass(frozen=True)
class RetrievalRequest:
    event_id: str
    occurred_at: str
    source: str
    email_identity: EmailIdentity
    correlation_id: str


def decode_push_message(wrapper: Any) -> RetrievalRequest:
    if not isinstance(wrapper, dict) or not isinstance(wrapper.get("message"), dict):
        raise InvalidMessage("Missing Pub/Sub message wrapper.")
    encoded = wrapper["message"].get("data")
    if not isinstance(encoded, str):
        raise InvalidMessage("Missing Pub/Sub message data.")
    try:
        raw = encoded.encode("ascii")
        decoded = base64.b64decode(raw, validate=True)
        event = json.loads(decoded.decode("utf-8"))
    except (binascii.Error, UnicodeEncodeError, UnicodeDecodeError, ValueError) as error:
        raise InvalidMessage("Pub/Sub message data is not valid base64-encoded JSON.") from error
    if not isinstance(event, dict):
        raise InvalidMessage("Retrieval request must be a JSON object.")
    return validate_request(event)


def validate_request(event: dict[str, Any]) -> RetrievalRequest:
    required = ("event_id", "event_type", "contract_version", "occurred_at", "source", "payload")
    if any(field not in event for field in required):
        raise InvalidMessage("Retrieval request is missing a required envelope field.")
    if set(event) - {*required, "correlation_id", "causation_id"}:
        raise InvalidMessage("Retrieval request contains unsupported envelope fields.")
    try:
        event_id = str(UUID(event["event_id"]))
    except (AttributeError, TypeError, ValueError) as error:
        raise InvalidMessage("Retrieval request event_id must be a UUID.") from error
    if event["event_type"] != REQUEST_TYPE or event["contract_version"] != CONTRACT_VERSION:
        raise InvalidMessage("Unsupported retrieval request type or contract version.")
    if not isinstance(event["occurred_at"], str):
        raise InvalidMessage("Retrieval request occurred_at must be a timestamp.")
    try:
        from datetime import datetime

        occurred_at = datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidMessage("Retrieval request occurred_at must be an ISO 8601 timestamp.") from error
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise InvalidMessage("Retrieval request occurred_at must include a timezone.")
    source = event["source"]
    if not isinstance(source, str) or not source.strip():
        raise InvalidMessage("Retrieval request source must be a non-empty string.")
    payload = event["payload"]
    if not isinstance(payload, dict):
        raise InvalidMessage("Retrieval request payload must be an object.")
    if set(payload) != {"email_identity"}:
        raise InvalidMessage("Retrieval request payload must contain email_identity.")
    identity = payload.get("email_identity")
    if not isinstance(identity, dict):
        raise InvalidMessage("Retrieval request email_identity must be an object.")
    required_identity_fields = {
        "provider",
        "source_account_id",
        "source_message_id",
    }
    if required_identity_fields - identity.keys():
        raise InvalidMessage("Retrieval request email_identity is missing required fields.")
    if identity.keys() - required_identity_fields - {"source_thread_id"}:
        raise InvalidMessage("Retrieval request email_identity contains unsupported fields.")
    provider = identity.get("provider")
    source_account_id = identity.get("source_account_id")
    source_message_id = identity.get("source_message_id")
    for field, value, max_length in (
        ("provider", provider, 64),
        ("source_account_id", source_account_id, 256),
        ("source_message_id", source_message_id, 256),
    ):
        if not isinstance(value, str) or not value.strip() or len(value) > max_length:
            raise InvalidMessage(f"Retrieval request email_identity.{field} is invalid.")
    source_thread_id = identity.get("source_thread_id")
    if "source_thread_id" in identity and (
        not isinstance(source_thread_id, str)
        or not source_thread_id.strip()
        or len(source_thread_id) > 256
    ):
        raise InvalidMessage(
            "Retrieval request email_identity.source_thread_id is invalid."
        )
    correlation_id = event.get("correlation_id", event_id)
    if not isinstance(correlation_id, str) or not correlation_id.strip():
        raise InvalidMessage("Retrieval request correlation_id must be a non-empty string.")
    return RetrievalRequest(
        event_id=event_id,
        occurred_at=event["occurred_at"],
        source=source,
        email_identity=EmailIdentity(
            provider=provider,
            source_account_id=source_account_id,
            source_message_id=source_message_id,
            source_thread_id=source_thread_id,
        ),
        correlation_id=correlation_id,
    )


def build_retrieved_event(request: RetrievalRequest, normalized: dict[str, Any]) -> dict[str, Any]:
    identity = request.email_identity
    if normalized.get("id") != identity.source_message_id:
        raise InvalidMessage("The retrieved message identity does not match the request.")
    event_identity = json.dumps(
        [
            request.event_id,
            identity.provider,
            identity.source_account_id,
            identity.source_message_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    event_id = str(uuid5(EVENT_NAMESPACE, event_identity))
    return {
        "event_id": event_id,
        "event_type": EVENT_TYPE,
        "contract_version": CONTRACT_VERSION,
        "occurred_at": request.occurred_at,
        "source": SOURCE,
        "correlation_id": request.correlation_id,
        "causation_id": request.event_id,
        "payload": {
            "email_identity": identity.as_payload(),
            "sender": normalized["sender"],
            "subject": normalized["subject"],
            "received_at": normalized["receivedAt"],
            "body_text": normalized["body"],
            "normalization_warnings": normalized["warnings"],
        },
    }


class EmailRetrievalService:
    def __init__(
        self,
        *,
        publisher: PublishClient,
        project_id: str,
        event_topic_id: str,
    ) -> None:
        self._publisher = publisher
        self._event_topic = f"projects/{project_id}/topics/{event_topic_id}"

    @classmethod
    def from_environment(cls) -> EmailRetrievalService:
        settings = Settings.from_environment()
        return cls(
            publisher=pubsub_v1.PublisherClient(),
            project_id=settings.project_id,
            event_topic_id=settings.event_topic_id,
        )

    def handle(self, request: RetrievalRequest) -> dict[str, Any]:
        if request.email_identity.provider != "gmail":
            raise InvalidMessage("Synthetic retrieval supports only the Gmail provider.")
        try:
            message = get_synthetic_message(request.email_identity.source_message_id)
        except ValueError as error:
            raise InvalidMessage(str(error)) from error
        normalized = normalize_gmail_message(message)
        event = build_retrieved_event(request, normalized)
        data = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        self._publisher.publish(
            self._event_topic,
            data,
            event_id=event["event_id"],
            event_type=EVENT_TYPE,
            contract_version=CONTRACT_VERSION,
            source=SOURCE,
        ).result(timeout=30)
        LOGGER.info("Published email retrieval event_id=%s event_type=%s", event["event_id"], EVENT_TYPE)
        return event
