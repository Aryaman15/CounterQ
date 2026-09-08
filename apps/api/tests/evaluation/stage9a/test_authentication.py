from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import User
from app.auth.providers.clerk import ClerkJWTVerifier
from app.auth.verifier import AuthenticationError
from app.config.settings import (
    REPOSITORY_ENV_FILE,
    REPOSITORY_ROOT,
    Settings,
    create_settings,
    get_settings,
)
from app.db.session import build_engine, get_session
from app.main import create_app

ISSUER = "https://stage9a.clerk.accounts.dev"
AUTHORIZED_PARTY = "https://counterq.example"
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_KEY_PEM = _PRIVATE_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode("ascii")
PUBLIC_KEY_PEM = _PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode("ascii")


def _claims(*, subject: str | None = None, **overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": subject or f"user_{uuid4().hex}",
        "sid": f"sess_{uuid4().hex}",
        "azp": AUTHORIZED_PARTY,
        "iat": now,
        "nbf": now - timedelta(seconds=1),
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return claims


def _token(*, key: str = PRIVATE_KEY_PEM, algorithm: str = "RS256", **claims: Any) -> str:
    return jwt.encode(_claims(**claims), key, algorithm=algorithm)


def _settings() -> Settings:
    return Settings(
        app_env="test",
        clerk_issuer=ISSUER,
        clerk_jwt_verification_key=SecretStr(PUBLIC_KEY_PEM),
        allowed_frontend_origins=AUTHORIZED_PARTY,
        auth_clock_skew_seconds=0,
    )


def _verifier() -> ClerkJWTVerifier:
    return ClerkJWTVerifier(
        issuer=ISSUER,
        verification_key=PUBLIC_KEY_PEM,
        authorized_parties=(AUTHORIZED_PARTY,),
        clock_skew_seconds=0,
    )


def test_backend_auth_configuration_remains_in_repository_root_env(tmp_path: Path) -> None:
    assert REPOSITORY_ENV_FILE == REPOSITORY_ROOT / ".env"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "COUNTERQ_AUTH_PROVIDER=clerk\n"
        f"COUNTERQ_CLERK_ISSUER={ISSUER}\n"
        'COUNTERQ_CLERK_JWT_VERIFICATION_KEY="-----BEGIN PUBLIC KEY-----\n'
        "test-only-key-material\n"
        '-----END PUBLIC KEY-----"\n'
        f"COUNTERQ_ALLOWED_FRONTEND_ORIGINS={AUTHORIZED_PARTY}\n",
        encoding="utf-8",
    )

    settings = create_settings(env_file)

    assert settings.auth_provider == "clerk"
    assert settings.clerk_issuer == ISSUER
    assert settings.clerk_jwt_verification_key is not None
    assert settings.clerk_jwt_verification_key.get_secret_value() == (
        "-----BEGIN PUBLIC KEY-----\n"
        "test-only-key-material\n"
        "-----END PUBLIC KEY-----"
    )
    assert settings.allowed_frontend_origin_values == (AUTHORIZED_PARTY,)


async def _request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    json: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> tuple[int, dict[str, Any]]:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings or _settings()
    engine = build_engine()
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def isolated_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = isolated_session
    headers = {"Authorization": f"Bearer {token}"} if token is not None else None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.request(method, path, headers=headers, json=json)
        return response.status_code, response.json()
    finally:
        await engine.dispose()


async def test_valid_clerk_jwt_resolves_an_external_principal() -> None:
    principal = await _verifier().verify(_token(subject="user_valid"))

    assert principal.provider == "clerk"
    assert principal.subject == "user_valid"
    assert principal.session_id is not None


@pytest.mark.parametrize(
    ("claims", "key"),
    [
        ({"exp": datetime.now(UTC) - timedelta(seconds=1)}, PRIVATE_KEY_PEM),
        ({"nbf": datetime.now(UTC) + timedelta(minutes=1)}, PRIVATE_KEY_PEM),
        ({"iss": "https://other-issuer.example"}, PRIVATE_KEY_PEM),
        ({"azp": "https://hostile.example"}, PRIVATE_KEY_PEM),
        ({"sts": "pending"}, PRIVATE_KEY_PEM),
        ({"sub": ""}, PRIVATE_KEY_PEM),
        ({}, rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")),
    ],
    ids=("expired", "future-nbf", "issuer", "azp", "pending", "subject", "signature"),
)
async def test_invalid_clerk_jwts_are_rejected(claims: dict[str, Any], key: str) -> None:
    token = _token(key=key, **claims)
    with pytest.raises(AuthenticationError):
        await _verifier().verify(token)

    status_code, body = await _request("GET", "/api/me", token=token)
    assert status_code == 401
    assert body == {"detail": "Authentication required"}


async def test_missing_required_claim_and_unexpected_algorithm_are_rejected() -> None:
    claims = _claims()
    claims.pop("sub")
    missing_subject = jwt.encode(claims, PRIVATE_KEY_PEM, algorithm="RS256")
    unsigned = jwt.encode(
        _claims(),
        "stage9a-shared-secret-with-at-least-thirty-two-bytes",
        algorithm="HS256",
    )

    with pytest.raises(AuthenticationError):
        await _verifier().verify(missing_subject)
    with pytest.raises(AuthenticationError):
        await _verifier().verify(unsigned)
    for token in (missing_subject, unsigned, "not-a-jwt"):
        status_code, body = await _request("GET", "/api/me", token=token)
        assert status_code == 401
        assert body == {"detail": "Authentication required"}


async def test_missing_bearer_token_is_401_even_when_auth_is_unconfigured() -> None:
    status_code, body = await _request("GET", "/api/me", settings=Settings(app_env="test"))

    assert status_code == 401
    assert body == {"detail": "Authentication required"}


async def test_valid_identity_is_concurrency_safe_and_stable() -> None:
    subject = f"user_concurrent_{uuid4().hex}"
    token = _token(subject=subject)

    first, second = await asyncio.gather(
        _request("GET", "/api/me", token=token),
        _request("GET", "/api/me", token=token),
    )

    assert first[0] == second[0] == 200
    assert first[1]["user_id"] == second[1]["user_id"]
    assert first[1]["onboarding_required"] is True
    engine = build_engine()
    try:
        async with async_sessionmaker(engine)() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.external_auth_provider == "clerk",
                    User.external_auth_subject == subject,
                )
            )
    finally:
        await engine.dispose()
    assert count == 1


@pytest.mark.parametrize(
    ("status_value", "deleted"),
    [("SUSPENDED", False), ("ACTIVE", True)],
)
async def test_inactive_or_soft_deleted_user_is_denied(
    status_value: str,
    deleted: bool,
) -> None:
    subject = f"user_denied_{uuid4().hex}"
    token = _token(subject=subject)
    created_status, _ = await _request("GET", "/api/me", token=token)
    assert created_status == 200
    engine = build_engine()
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            await session.execute(
                update(User)
                .where(
                    User.external_auth_provider == "clerk",
                    User.external_auth_subject == subject,
                )
                .values(
                    status=status_value,
                    deleted_at=datetime.now(UTC) if deleted else None,
                )
            )
    finally:
        await engine.dispose()

    status_code, body = await _request("GET", "/api/me", token=token)

    assert status_code == 403
    assert body == {"detail": "CounterQ account is unavailable"}


async def test_profile_onboarding_round_trip_is_server_bound_and_versioned() -> None:
    token = _token(subject=f"user_profile_{uuid4().hex}")
    status_code, initial = await _request("GET", "/api/me", token=token)
    assert status_code == 200
    assert initial["profile"] is None
    assert initial["onboarding_required"] is True

    payload = {
        "display_name": "  Ada  ",
        "preferred_language": "python",
        "default_interview_mode": "SIMULATION",
        "interview_level": "NEW_GRAD",
        "target_role": "  Backend engineer  ",
        "timezone": "Asia/Calcutta",
    }
    status_code, created = await _request("PUT", "/api/me/profile", token=token, json=payload)
    assert status_code == 200
    assert created["user_id"] == initial["user_id"]
    assert created["onboarding_required"] is False
    assert created["profile"]["display_name"] == "Ada"
    assert created["profile"]["target_role"] == "Backend engineer"
    assert created["profile"]["profile_version"] == 1

    payload["preferred_language"] = "java"
    status_code, updated = await _request("PUT", "/api/me/profile", token=token, json=payload)
    assert status_code == 200
    assert updated["profile"]["preferred_language"] == "java"
    assert updated["profile"]["profile_version"] == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("preferred_language", "javascript"),
        ("default_interview_mode", "PRACTICE"),
        ("interview_level", "STAFF"),
        ("user_id", str(uuid4())),
    ],
)
async def test_profile_rejects_invalid_enums_and_client_selected_identity(
    field: str,
    value: str,
) -> None:
    payload = {
        "preferred_language": "python",
        "default_interview_mode": "SIMULATION",
        "interview_level": "NEW_GRAD",
        field: value,
    }

    status_code, _body = await _request(
        "PUT",
        "/api/me/profile",
        token=_token(),
        json=payload,
    )

    assert status_code == 422


@pytest.mark.parametrize(
    "missing_field",
    ("preferred_language", "default_interview_mode", "interview_level"),
)
async def test_profile_requires_each_interview_calibration_field(missing_field: str) -> None:
    payload = {
        "preferred_language": "python",
        "default_interview_mode": "SIMULATION",
        "interview_level": "NEW_GRAD",
    }
    payload.pop(missing_field)

    status_code, _body = await _request(
        "PUT",
        "/api/me/profile",
        token=_token(),
        json=payload,
    )

    assert status_code == 422


async def test_profile_mutation_cannot_target_another_authenticated_user() -> None:
    first_token = _token(subject=f"user_profile_owner_{uuid4().hex}")
    second_token = _token(subject=f"user_profile_other_{uuid4().hex}")
    _, first = await _request("GET", "/api/me", token=first_token)
    _, second = await _request("GET", "/api/me", token=second_token)
    payload = {
        "preferred_language": "cpp",
        "default_interview_mode": "COACH",
        "interview_level": "INTERN",
        "user_id": first["user_id"],
    }

    status_code, _body = await _request(
        "PUT",
        "/api/me/profile",
        token=second_token,
        json=payload,
    )
    _, first_after = await _request("GET", "/api/me", token=first_token)
    _, second_after = await _request("GET", "/api/me", token=second_token)

    assert first["user_id"] != second["user_id"]
    assert status_code == 422
    assert first_after["profile"] is None
    assert second_after["profile"] is None


async def test_cors_allows_configured_origin_and_bearer_header_only() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options(
            "/api/me",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


@pytest.mark.parametrize(
    "origins",
    ("*", "", "https://counterq.example/app", "https://counterq.example?tenant=one"),
)
def test_cors_configuration_rejects_non_origin_and_wildcard_values(origins: str) -> None:
    with pytest.raises(ValueError):
        Settings(allowed_frontend_origins=origins)
