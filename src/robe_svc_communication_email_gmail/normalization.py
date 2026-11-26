from __future__ import annotations

import base64
import binascii
import re
from datetime import UTC, datetime
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

_ADDRESS = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$"
)


class _TextFromHtml(HTMLParser):
    _BLOCKED = {"head", "script", "style", "template"}
    _BREAKS = {"br", "blockquote", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "p", "pre", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._blocked_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if self._blocked_depth:
            if tag in self._BLOCKED:
                self._blocked_depth += 1
            return
        if tag in self._BLOCKED:
            self._blocked_depth = 1
        elif tag in self._BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._blocked_depth:
            if tag in self._BLOCKED:
                self._blocked_depth -= 1
            return
        if tag in self._BREAKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._blocked_depth:
            self.parts.append(data)


def _clean_text(value: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t\f\v]+", " ", value.replace("\r\n", "\n").replace("\r", "\n"))).strip()


def _html_to_text(value: str) -> str:
    parser = _TextFromHtml()
    parser.feed(value)
    parser.close()
    return _clean_text("".join(parser.parts))


def _headers(part: Any, warnings: set[str]) -> list[dict[str, str]]:
    if not isinstance(part, dict) or part.get("headers") is None:
        return []
    raw_headers = part["headers"]
    if not isinstance(raw_headers, list):
        warnings.add("invalid_headers")
        return []
    result: list[dict[str, str]] = []
    for header in raw_headers:
        if not isinstance(header, dict) or not isinstance(header.get("name"), str) or not isinstance(header.get("value"), str):
            warnings.add("invalid_headers")
            continue
        result.append(header)
    return result


def _header_value(headers: list[dict[str, str]], name: str, warnings: set[str]) -> str | None:
    values = [
        header["value"].strip()
        for header in headers
        if header["name"].strip().lower() == name
    ]
    if len(values) > 1:
        warnings.add(f"duplicate_{name.replace('-', '_')}_header")
        return None
    return values[0] if values else None


def _sender(value: str | None, warnings: set[str]) -> dict[str, str | None]:
    if not value:
        return {"name": None, "address": None}
    name, address = parseaddr(value)
    address = address.strip()
    if not _ADDRESS.fullmatch(address) or ".." in address:
        warnings.add("invalid_sender")
        return {"name": None, "address": None}
    name = name.strip().strip('"').strip() or None
    return {"name": name, "address": address.lower()}


def _received_at(
    message: dict[str, Any], headers: list[dict[str, str]], warnings: set[str]
) -> str | None:
    raw_internal_date = message.get("internalDate")
    if raw_internal_date is not None:
        try:
            if isinstance(raw_internal_date, bool):
                raise ValueError
            milliseconds = int(raw_internal_date)
            if milliseconds < 0 or str(milliseconds) != str(raw_internal_date).strip():
                raise ValueError
            return datetime.fromtimestamp(milliseconds / 1000, tz=UTC).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, TypeError, ValueError):
            warnings.add("invalid_internal_date")
    date_value = _header_value(headers, "date", warnings)
    if not date_value:
        return None
    try:
        parsed = parsedate_to_datetime(date_value)
    except (TypeError, ValueError, OverflowError):
        warnings.add("invalid_date_header")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        warnings.add("invalid_date_header")
        return None
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _decode_body(part: dict[str, Any], headers: list[dict[str, str]], warnings: set[str]) -> str | None:
    body = part.get("body")
    if body is None:
        return None
    if not isinstance(body, dict):
        warnings.add("invalid_body")
        return None
    raw_data = body.get("data")
    if raw_data is None:
        if body.get("attachmentId") or body.get("size", 0):
            warnings.add("external_body_unavailable")
        return None
    if not isinstance(raw_data, str) or not re.fullmatch(r"[A-Za-z0-9_-]*={0,2}", raw_data):
        warnings.add("invalid_body_encoding")
        return None
    unpadded = raw_data.rstrip("=")
    if len(unpadded) % 4 == 1 or ("=" in raw_data and len(raw_data) % 4):
        warnings.add("invalid_body_encoding")
        return None
    try:
        decoded = base64.b64decode(
            unpadded + "=" * (-len(unpadded) % 4), altchars=b"-_", validate=True
        )
    except (binascii.Error, ValueError):
        warnings.add("invalid_body_encoding")
        return None
    if base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != unpadded:
        warnings.add("invalid_body_encoding")
        return None
    content_type = _header_value(headers, "content-type", warnings)
    if content_type:
        content = Message()
        content["content-type"] = content_type
        charset = content.get_content_charset() or "utf-8"
    else:
        charset = "utf-8"
    try:
        return decoded.decode(charset, errors="strict")
    except (LookupError, UnicodeDecodeError):
        warnings.add("invalid_body_charset_or_text")
        return None


def _body_from_part(
    part: Any, warnings: set[str], *, depth: int = 0, visited: set[int] | None = None
) -> tuple[str, str] | None:
    if not isinstance(part, dict):
        warnings.add("invalid_mime_part")
        return None
    if visited is None:
        visited = set()
    part_identity = id(part)
    if part_identity in visited or depth > 100 or len(visited) >= 10000:
        warnings.add("mime_structure_limit")
        return None
    visited.add(part_identity)
    headers = _headers(part, warnings)
    disposition = _header_value(headers, "content-disposition", warnings) or ""
    filename = part.get("filename")
    if (isinstance(filename, str) and filename.strip()) or re.match(r"\s*attachment\b", disposition, re.I):
        return None
    mime_type = part.get("mimeType")
    mime_type = mime_type.split(";", 1)[0].strip().lower() if isinstance(mime_type, str) else ""
    if mime_type == "message/rfc822":
        return None
    if mime_type in {"text/plain", "text/html"}:
        decoded = _decode_body(part, headers, warnings)
        if decoded is None:
            return None
        text = _html_to_text(decoded) if mime_type == "text/html" else _clean_text(decoded)
        return (text, "plain" if mime_type == "text/plain" else "html") if text else None
    children = part.get("parts")
    if children is None:
        body = part.get("body")
        if isinstance(body, dict) and body.get("data") and not mime_type:
            warnings.add("missing_body_mime_type")
        return None
    if not isinstance(children, list):
        warnings.add("invalid_mime_parts")
        return None
    extracted = [
        result
        for child in children
        if (result := _body_from_part(child, warnings, depth=depth + 1, visited=visited))
    ]
    if not extracted:
        return None
    if mime_type == "multipart/alternative":
        return next((child for child in extracted if child[1] == "plain"), extracted[0])
    combined = "\n\n".join(child[0] for child in extracted)
    kind = "plain" if any(child[1] == "plain" for child in extracted) else "html"
    return combined, kind


def normalize_gmail_message(message: Any) -> dict[str, Any]:
    warnings: set[str] = set()
    if not isinstance(message, dict):
        warnings.add("invalid_message")
        message = {}
    payload = message.get("payload")
    if payload is not None and not isinstance(payload, dict):
        warnings.add("invalid_payload")
        payload = None
    headers = _headers(payload, warnings)
    message_id = message.get("id")
    if not isinstance(message_id, str) or not message_id.strip():
        message_id = None
        warnings.add("missing_or_invalid_id")
    thread_id = message.get("threadId")
    if not isinstance(thread_id, str) or not thread_id.strip():
        if message.get("threadId") is not None:
            warnings.add("invalid_thread_id")
        thread_id = None
    sender = _sender(_header_value(headers, "from", warnings), warnings)
    subject = _header_value(headers, "subject", warnings)
    received_at = _received_at(message, headers, warnings)
    body = _body_from_part(payload, warnings)
    return {
        "schemaVersion": "1.0",
        "id": message_id,
        "threadId": thread_id,
        "sender": sender,
        "subject": subject,
        "receivedAt": received_at,
        "body": body[0] if body else "",
        "warnings": sorted(warnings),
    }
