from __future__ import annotations

from collections.abc import Collection
from typing import Any

import jwt
from jwt import InvalidTokenError

from app.auth.principal import ExternalPrincipal
from app.auth.verifier import AuthenticationError

CLERK_PROVIDER = "clerk"
CLERK_SIGNING_ALGORITHM = "RS256"


class ClerkJWTVerifier:
    """Verify Clerk session JWTs locally with the configured instance public key."""

    def __init__(
        self,
        *,
        issuer: str,
        verification_key: str,
        authorized_parties: Collection[str],
        clock_skew_seconds: int = 5,
    ) -> None:
        if not issuer.strip() or not verification_key.strip():
            raise ValueError("Clerk issuer and JWT verification key are required")
        self._issuer = issuer.strip().rstrip("/")
        self._verification_key = verification_key
        self._authorized_parties = frozenset(
            party.strip().rstrip("/") for party in authorized_parties if party.strip()
        )
        if not self._authorized_parties:
            raise ValueError("At least one authorized frontend party is required")
        self._clock_skew_seconds = clock_skew_seconds

    async def verify(self, token: str) -> ExternalPrincipal:
        if not token or len(token) > 16_384:
            raise AuthenticationError("Bearer token is malformed")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != CLERK_SIGNING_ALGORITHM:
                raise AuthenticationError("Bearer token uses an unexpected signing algorithm")
            claims: dict[str, Any] = jwt.decode(
                token,
                self._verification_key,
                algorithms=[CLERK_SIGNING_ALGORITHM],
                issuer=self._issuer,
                leeway=self._clock_skew_seconds,
                options={
                    "require": ["exp", "iat", "nbf", "iss", "sub"],
                    "verify_aud": False,
                },
            )
        except AuthenticationError:
            raise
        except (InvalidTokenError, TypeError, ValueError) as exc:
            raise AuthenticationError("Bearer token could not be verified") from exc

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip() or len(subject) > 256:
            raise AuthenticationError("Bearer token subject is invalid")
        authorized_party = claims.get("azp")
        if authorized_party is not None and (
            not isinstance(authorized_party, str)
            or authorized_party.rstrip("/") not in self._authorized_parties
        ):
            raise AuthenticationError("Bearer token authorized party is invalid")
        if claims.get("sts") == "pending":
            raise AuthenticationError("Clerk session is not fully active")
        session_id = claims.get("sid")
        if session_id is not None and not isinstance(session_id, str):
            raise AuthenticationError("Bearer token session identifier is invalid")
        return ExternalPrincipal(
            provider=CLERK_PROVIDER,
            subject=subject.strip(),
            session_id=session_id,
        )
