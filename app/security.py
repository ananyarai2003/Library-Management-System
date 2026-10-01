"""API-key authentication for write endpoints."""
import hmac

from fastapi import Depends, Header, HTTPException, Request


def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    expected = request.app.state.settings.api_key
    if not expected:
        raise HTTPException(503, "API key is not configured on the server")
    if x_api_key is None or not hmac.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(401, "Invalid or missing API key")


WriteAccess = Depends(require_api_key)
