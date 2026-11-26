from __future__ import annotations

import logging
from threading import Lock
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from robe_svc_communication_email_gmail.service import (
    EmailRetrievalService,
    InvalidMessage,
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
