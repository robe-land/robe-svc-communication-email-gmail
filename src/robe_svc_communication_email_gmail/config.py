from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    project_id: str
    request_topic_id: str
    event_topic_id: str
    request_subscription_id: str
    dead_letter_topic_id: str
    dead_letter_subscription_id: str
    push_endpoint: str

    @classmethod
    def from_environment(cls) -> Settings:
        names = {
            "project_id": "PUBSUB_PROJECT_ID",
            "request_topic_id": "REQUEST_TOPIC_ID",
            "event_topic_id": "EVENT_TOPIC_ID",
            "request_subscription_id": "REQUEST_SUBSCRIPTION_ID",
            "dead_letter_topic_id": "DEAD_LETTER_TOPIC_ID",
            "dead_letter_subscription_id": "DEAD_LETTER_SUBSCRIPTION_ID",
            "push_endpoint": "PUSH_ENDPOINT",
        }
        values = {field: os.environ.get(name, "").strip() for field, name in names.items()}
        missing = [names[field] for field, value in values.items() if not value]
        if missing:
            raise RuntimeError(f"Missing required service configuration: {', '.join(missing)}")
        return cls(**values)
