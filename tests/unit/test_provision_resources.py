from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import AlreadyExists

from robe_svc_communication_email_gmail.provision_resources import (
    ACK_DEADLINE_SECONDS,
    MAX_DELIVERY_ATTEMPTS,
    MAX_RETRY_BACKOFF_SECONDS,
    MIN_RETRY_BACKOFF_SECONDS,
    _ensure_request_subscription,
)


def existing_subscription() -> SimpleNamespace:
    return SimpleNamespace(
        topic="projects/test/topics/requests",
        push_config=SimpleNamespace(push_endpoint="http://service/push"),
        ack_deadline_seconds=ACK_DEADLINE_SECONDS,
        retry_policy=SimpleNamespace(
            minimum_backoff=SimpleNamespace(
                seconds=MIN_RETRY_BACKOFF_SECONDS, nanos=0
            ),
            maximum_backoff=SimpleNamespace(
                seconds=MAX_RETRY_BACKOFF_SECONDS, nanos=0
            ),
        ),
        dead_letter_policy=SimpleNamespace(
            dead_letter_topic="projects/test/topics/dead-letter",
            max_delivery_attempts=MAX_DELIVERY_ATTEMPTS,
        ),
    )


def ensure_existing(subscription: SimpleNamespace) -> None:
    subscriber = Mock()
    subscriber.create_subscription.side_effect = AlreadyExists("already exists")
    subscriber.get_subscription.return_value = subscription

    _ensure_request_subscription(
        subscriber,
        subscription="projects/test/subscriptions/requests",
        topic="projects/test/topics/requests",
        dead_letter_topic="projects/test/topics/dead-letter",
        push_endpoint="http://service/push",
    )


def test_existing_subscription_with_declared_reliability_settings_is_accepted() -> None:
    ensure_existing(existing_subscription())


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("ack_deadline_seconds", 31, "acknowledgment deadline"),
        ("minimum_backoff.seconds", 11, "minimum retry backoff"),
        ("minimum_backoff.nanos", 1, "minimum retry backoff"),
        ("maximum_backoff.seconds", 601, "maximum retry backoff"),
        ("maximum_backoff.nanos", 1, "maximum retry backoff"),
    ],
)
def test_existing_subscription_with_different_reliability_settings_is_rejected(
    field: str, value: int, error: str
) -> None:
    subscription = existing_subscription()
    if field == "ack_deadline_seconds":
        subscription.ack_deadline_seconds = value
    else:
        duration_name, value_name = field.split(".")
        setattr(getattr(subscription.retry_policy, duration_name), value_name, value)

    with pytest.raises(RuntimeError, match=error):
        ensure_existing(subscription)
