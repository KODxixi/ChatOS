from __future__ import annotations

import hmac
import re
import secrets
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse


SESSION_COOKIE_NAME = "wechat_ai_session"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_ALLOWED_HOST = re.compile(
    r"^(?:127\.0\.0\.1|localhost|\[::1\])(?::([0-9]{1,5}))?$",
    flags=re.IGNORECASE,
)
_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'none'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
    )
)


def install_local_security(
    app: FastAPI,
    *,
    token: str | None = None,
) -> None:
    if token is not None and (not isinstance(token, str) or not token):
        raise ValueError("token must be a non-empty string")
    session_token = token if token is not None else secrets.token_urlsafe(32)
    session_token_bytes = session_token.encode("utf-8")

    @app.middleware("http")
    async def local_security(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not _is_allowed_host(request.headers.get("host", "")):
            response = _error_response(
                status_code=400,
                code="INVALID_HOST",
                message="请求 Host 不受信任。",
            )
            _apply_security_headers(response, request.url.path)
            return response

        cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
        has_session = (
            cookie_value is not None
            and hmac.compare_digest(
                cookie_value.encode("utf-8"),
                session_token_bytes,
            )
        )
        method = request.method.upper()

        if method not in _SAFE_METHODS and not has_session:
            response = _error_response(
                status_code=403,
                code="SESSION_REQUIRED",
                message="需要有效的本地会话。",
            )
        else:
            response = await call_next(request)
            if method in _SAFE_METHODS and not has_session:
                response.set_cookie(
                    key=SESSION_COOKIE_NAME,
                    value=session_token,
                    httponly=True,
                    samesite="strict",
                    path="/",
                )

        _apply_security_headers(response, request.url.path)
        return response


def _is_allowed_host(host: str) -> bool:
    match = _ALLOWED_HOST.fullmatch(host)
    if match is None:
        return False
    port = match.group(1)
    return port is None or int(port) <= 65_535


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": False,
                "details": {},
            }
        },
    )


def _apply_security_headers(response: Response, path: str) -> None:
    response.headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if path == "/api" or path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
