from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import CurrentUser, ExternalPrincipal
from app.auth.providers.clerk import ClerkJWTVerifier
from app.auth.service import AccountUnavailable, UserIdentityService
from app.auth.verifier import (
    AuthenticationConfigurationError,
    AuthenticationError,
    PrincipalVerifier,
)
from app.config.settings import Settings, get_settings
from app.db.session import get_session

bearer_scheme = HTTPBearer(auto_error=False)


def get_principal_verifier(
    settings: Annotated[Settings, Depends(get_settings)],
) -> PrincipalVerifier:
    if settings.auth_provider != "clerk":
        raise AuthenticationConfigurationError("Configured authentication provider is unsupported")
    key = settings.clerk_jwt_verification_key
    if not settings.clerk_issuer or key is None:
        raise AuthenticationConfigurationError("Clerk JWT verification is not configured")
    try:
        return ClerkJWTVerifier(
            issuer=settings.clerk_issuer,
            verification_key=key.get_secret_value(),
            authorized_parties=settings.allowed_frontend_origin_values,
            clock_skew_seconds=settings.auth_clock_skew_seconds,
        )
    except ValueError as exc:
        raise AuthenticationConfigurationError(str(exc)) from exc


async def get_external_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ExternalPrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthenticated()
    try:
        verifier = get_principal_verifier(settings)
        return await verifier.verify(credentials.credentials)
    except AuthenticationConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is not configured",
        ) from exc
    except AuthenticationError as exc:
        raise _unauthenticated() from exc


async def get_current_user(
    principal: Annotated[ExternalPrincipal, Depends(get_external_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CurrentUser:
    try:
        async with session.begin():
            user = await UserIdentityService(session).resolve_or_create_current_user(principal)
            return CurrentUser(id=user.id, status=user.status)
    except AccountUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CounterQ account is unavailable",
        ) from exc


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )
