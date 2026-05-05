from __future__ import annotations

from fastapi import Header, HTTPException, Request, status

from app.tenants import Tenant


async def require_tenant(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Tenant:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
        )
    token = authorization[len("Bearer "):]
    tenant = request.app.state.registry.resolve(token)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
        )
    return tenant
