from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response

from robe_svc_communication_email_gmail.service import (
    EmailRetrievalService,
    InvalidMessage,
    CONTRACT_VERSION,
    REQUEST_TYPE,
    decode_push_message,
)

LOGGER = logging.getLogger(__name__)


def create_app(service: EmailRetrievalService | None = None) -> FastAPI:
    app = FastAPI()
    active_service = service
    service_lock = Lock()

    def get_service() -> EmailRetrievalService:
        nonlocal active_service
        if active_service is None:
            with service_lock:
                if active_service is None:
                    active_service = EmailRetrievalService.from_environment()
        return active_service

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/.well-known/robe-service.json")
    def workbench_descriptor() -> dict[str, Any]:
        from robe_svc_communication_email_gmail.config import Settings

        config = Settings.from_environment()
        return {
            "schema_version": 1,
            "service": {
                "id": "robe-svc-communication-email-gmail",
                "name": "Gmail Email Retrieval",
                "description": "Receives email retrieval requests and publishes normalized email events.",
            },
            "health": {"path": "/healthz"},
            "messaging": {
                "provider": "google-cloud-pubsub",
                "project_id": config.project_id,
                "topics": [
                    {
                        "id": config.request_topic_id,
                        "name": "Email retrieval requests",
                        "role": "consumes",
                    },
                    {
                        "id": config.event_topic_id,
                        "name": "Retrieved email events",
                        "role": "publishes",
                    },
                    {
                        "id": config.dead_letter_topic_id,
                        "name": "Email retrieval dead letters",
                        "role": "dead_letter",
                    },
                ],
                "synthetic_templates": [
                    {
                        "id": "retrieval-request",
                        "name": "Synthetic email retrieval request",
                        "topic_id": config.request_topic_id,
                        "template_path": "/.well-known/synthetic/retrieval-request",
                        "contract_path": "/.well-known/contracts/email-retrieval-requested.v1.schema.json",
                    }
                ],
                "subscriptions": [
                    {
                        "id": config.request_subscription_id,
                        "name": "Gmail retrieval requests",
                        "topic_id": config.request_topic_id,
                        "delivery": "push",
                        "endpoint": "/pubsub/push",
                    },
                    {
                        "id": config.dead_letter_subscription_id,
                        "name": "Gmail retrieval dead letters",
                        "topic_id": config.dead_letter_topic_id,
                        "delivery": "pull",
                    },
                ],
            },
        }

    @app.get("/.well-known/synthetic/retrieval-request")
    def synthetic_retrieval_request() -> dict[str, Any]:
        event_id = str(uuid4())
        return {
            "event_id": event_id,
            "event_type": REQUEST_TYPE,
            "contract_version": CONTRACT_VERSION,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "source": "local-workbench",
            "correlation_id": event_id,
            "payload": {
                "email_identity": {
                    "provider": "gmail",
                    "source_account_id": "synthetic-account-001",
                    "source_message_id": "gmail-fixture-001",
                    "source_thread_id": "gmail-thread-fixture-001",
                }
            },
        }

    @app.get("/.well-known/contracts/email-retrieval-requested.v1.schema.json")
    def retrieval_request_contract() -> FileResponse:
        name = "email-retrieval-requested.v1.schema.json"
        installed = Path(sys.prefix) / "share" / "robe-svc-communication-email-gmail" / "contracts" / name
        source = Path(__file__).resolve().parents[2] / "contracts" / name
        path = installed if installed.is_file() else source
        if not path.is_file():
            raise RuntimeError("The installed retrieval-request contract is unavailable.")
        return FileResponse(path, media_type="application/schema+json")

    @app.post("/pubsub/push")
    def receive_push(wrapper: dict[str, Any]) -> Response:
        try:
            request = decode_push_message(wrapper)
            event = get_service().handle(request)
        except InvalidMessage as error:
            LOGGER.warning("Rejected invalid retrieval request: %s", error)
            return JSONResponse(status_code=400, content={"detail": "Invalid retrieval request."})
        LOGGER.info("Acknowledged retrieval request event_id=%s", request.event_id)
        return Response(status_code=204, headers={"X-Emitted-Event-Id": event["event_id"]})

    return app


app = create_app()
