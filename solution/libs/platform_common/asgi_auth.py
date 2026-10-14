"""Bearer-token authentication boundary for ASGI apps (used in front of the MCP Streamable HTTP endpoints)."""

from __future__ import annotations

import json
import secrets

from starlette.types import ASGIApp, Receive, Scope, Send

PUBLIC_PATHS = ("/health",)


class BearerAuthMiddleware:
    """Rejects MCP requests without the configured bearer token. ``/health`` stays public."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.token or scope.get("path", "").startswith(PUBLIC_PATHS):
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        scheme, _, presented = headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer" and presented and secrets.compare_digest(presented, self.token):
            await self.app(scope, receive, send)
            return
        body = json.dumps(
            {"error": {"code": "UNAUTHORIZED", "message": "Invalid or missing MCP bearer token"}}
        )
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")],
            }
        )
        await send({"type": "http.response.body", "body": body.encode()})
