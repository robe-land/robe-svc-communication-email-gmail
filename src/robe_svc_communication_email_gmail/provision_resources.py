from __future__ import annotations

import logging

from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1

from robe_svc_communication_email_gmail.config import Settings

LOGGER = logging.getLogger(__name__)
ACK_DEADLINE_SECONDS = 30
MIN_RETRY_BACKOFF_SECONDS = 10
MAX_RETRY_BACKOFF_SECONDS = 600
MAX_DELIVERY_ATTEMPTS = 5


def _duration_matches(duration: object | None, seconds: int) -> bool:
    return (
        getattr(duration, "seconds", None) == seconds
        and getattr(duration, "nanos", None) == 0
    )


def _ensure_topic(client: pubsub_v1.PublisherClient, path: str) -> None:
    try:
        client.create_topic(request={"name": path})
    except AlreadyExists:
        client.get_topic(request={"topic": path})


def _ensure_request_subscription(
    subscriber: pubsub_v1.SubscriberClient,
    *,
    subscription: str,
    topic: str,
    dead_letter_topic: str,
    push_endpoint: str,
) -> None:
    try:
        subscriber.create_subscription(
            request={
                "name": subscription,
                "topic": topic,
                "ack_deadline_seconds": ACK_DEADLINE_SECONDS,
                "push_config": {"push_endpoint": push_endpoint},
                "retry_policy": {
                    "minimum_backoff": {"seconds": MIN_RETRY_BACKOFF_SECONDS},
                    "maximum_backoff": {"seconds": MAX_RETRY_BACKOFF_SECONDS},
                },
                "dead_letter_policy": {
                    "dead_letter_topic": dead_letter_topic,
                    "max_delivery_attempts": MAX_DELIVERY_ATTEMPTS,
                },
            }
        )
        return
    except AlreadyExists:
        existing = subscriber.get_subscription(request={"subscription": subscription})
    if existing.topic != topic:
        raise RuntimeError(f"Existing subscription {subscription} points to a different topic.")
    if existing.push_config.push_endpoint != push_endpoint:
        raise RuntimeError(f"Existing subscription {subscription} has a different push endpoint.")
    if existing.ack_deadline_seconds != ACK_DEADLINE_SECONDS:
        raise RuntimeError(
            f"Existing subscription {subscription} has a different acknowledgment deadline."
        )
    retry_policy = existing.retry_policy
    if not _duration_matches(
        getattr(retry_policy, "minimum_backoff", None), MIN_RETRY_BACKOFF_SECONDS
    ):
        raise RuntimeError(
            f"Existing subscription {subscription} has a different minimum retry backoff."
        )
    if not _duration_matches(
        getattr(retry_policy, "maximum_backoff", None), MAX_RETRY_BACKOFF_SECONDS
    ):
        raise RuntimeError(
            f"Existing subscription {subscription} has a different maximum retry backoff."
        )
    if existing.dead_letter_policy.dead_letter_topic != dead_letter_topic:
        raise RuntimeError(f"Existing subscription {subscription} has a different dead-letter topic.")
    if existing.dead_letter_policy.max_delivery_attempts != MAX_DELIVERY_ATTEMPTS:
        raise RuntimeError(f"Existing subscription {subscription} has a different delivery-attempt limit.")


def _ensure_dead_letter_subscription(
    subscriber: pubsub_v1.SubscriberClient, *, subscription: str, topic: str
) -> None:
    try:
        subscriber.create_subscription(
            request={"name": subscription, "topic": topic, "ack_deadline_seconds": 60}
        )
    except AlreadyExists:
        existing = subscriber.get_subscription(request={"subscription": subscription})
        if existing.topic != topic:
            raise RuntimeError(f"Existing subscription {subscription} points to a different topic.")


def provision(settings: Settings) -> None:
    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()
    request_topic = publisher.topic_path(settings.project_id, settings.request_topic_id)
    event_topic = publisher.topic_path(settings.project_id, settings.event_topic_id)
    dead_letter_topic = publisher.topic_path(settings.project_id, settings.dead_letter_topic_id)
    request_subscription = subscriber.subscription_path(
        settings.project_id, settings.request_subscription_id
    )
    dead_letter_subscription = subscriber.subscription_path(
        settings.project_id, settings.dead_letter_subscription_id
    )
    try:
        for topic in (request_topic, event_topic, dead_letter_topic):
            _ensure_topic(publisher, topic)
        _ensure_request_subscription(
            subscriber,
            subscription=request_subscription,
            topic=request_topic,
            dead_letter_topic=dead_letter_topic,
            push_endpoint=settings.push_endpoint,
        )
        _ensure_dead_letter_subscription(
            subscriber, subscription=dead_letter_subscription, topic=dead_letter_topic
        )
    finally:
        publisher.transport.close()
        subscriber.transport.close()
    LOGGER.info(
        "Provisioned service-owned Pub/Sub topics and subscriptions project=%s",
        settings.project_id,
    )


def main() -> None:
    provision(Settings.from_environment())


if __name__ == "__main__":
    main()
