from __future__ import annotations

from typing import Protocol

from app.auth.principal import ExternalPrincipal


class AuthenticationError(ValueError):
    """A candidate credential cannot establish an authenticated principal."""


class AuthenticationConfigurationError(RuntimeError):
    """Production authentication is not configured safely."""


class PrincipalVerifier(Protocol):
    async def verify(self, token: str) -> ExternalPrincipal: ...
