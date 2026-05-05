import os

from fastapi import Header, HTTPException, status

BEARER_TOKEN = os.environ.get("BEARER_TOKEN", "")


def require_bearer_token(authorization: str | None = Header(default=None)) -> None:
    expected = f"Bearer {BEARER_TOKEN}"
    if not BEARER_TOKEN or authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
        )
