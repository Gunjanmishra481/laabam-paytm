"""Request hygiene for every API call (security plan §3, §8). A pure ASGI middleware, so it sees the raw request.

- JSON bodies: at most 64 KB, and only with Content-Type application/json (415 otherwise). Uploads (audio, photos)
  have their own routes and caps and are not JSON.
- A query key given twice is refused (400): no parameter pollution.
- API responses get Cache-Control: no-store and X-Content-Type-Options: nosniff; the server banner is dropped.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qsl

JSON_LIMIT = 64 * 1024
BODY_METHODS = {b"POST", b"PUT", b"PATCH", b"DELETE"}
# routes whose body is not JSON (they check their own content type and size)
RAW_BODY = ("/v1/bills/voice", "/v1/ask/voice", "/v1/purchases/photo", "/v1/uploads/")


def _is_raw(path: str) -> bool:
    return path.endswith("/say/voice") or any(path == p or path.startswith(p) for p in RAW_BODY if p.endswith("/")) \
        or path in RAW_BODY


class RequestGuard:
    def __init__(self, app) -> None:
        self.app = app

    async def _reply(self, send, status: int, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode()
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path: str = scope["path"]
        api = path.startswith(("/v1/", "/sandbox/", "/api/"))
        keys = [k for k, _ in parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)]
        if api and len(keys) != len(set(keys)):
            return await self._reply(send, 400, "A query parameter was given twice.")
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        method = scope["method"].encode()
        if api and method in BODY_METHODS and not _is_raw(path):
            length = headers.get(b"content-length")
            has_body = (length is not None and length != b"0") or b"transfer-encoding" in headers
            ctype = headers.get(b"content-type", b"").split(b";")[0].strip().lower()
            if has_body and ctype != b"application/json":
                return await self._reply(send, 415, "Send JSON (Content-Type: application/json).")
            if length is not None:
                try:
                    if int(length) > JSON_LIMIT:
                        return await self._reply(send, 413, "That request is too large.")
                except ValueError:
                    return await self._reply(send, 400, "Bad Content-Length.")
            seen = 0
            too_big = False

            async def capped():
                nonlocal seen, too_big
                msg = await receive()
                if msg["type"] == "http.request":
                    seen += len(msg.get("body", b""))
                    if seen > JSON_LIMIT:
                        too_big = True
                        return {"type": "http.request", "body": b"", "more_body": False}
                return msg

            inner_receive = capped
        else:
            inner_receive = receive

        async def send_wrapper(message):
            if message["type"] == "http.response.start" and api:
                hs = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"server"]
                names = {k.lower() for k, _ in hs}
                if b"cache-control" not in names:
                    hs.append((b"cache-control", b"no-store"))
                if b"x-content-type-options" not in names:
                    hs.append((b"x-content-type-options", b"nosniff"))
                message = {**message, "headers": hs}
            await send(message)

        await self.app(scope, inner_receive, send_wrapper)
