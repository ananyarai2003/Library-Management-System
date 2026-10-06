"""API-key check for write endpoints. Disabled when LMS_API_KEY is unset."""
import hmac

from fastapi import Header

from app import config
from app.errors import UnauthorizedError


def require_api_key(x_api_key: str = Header(default="")) -> None:
    expected = config.api_key()
    if expected and not hmac.compare_digest(x_api_key, expected):
        raise UnauthorizedError("Invalid or missing API key")
