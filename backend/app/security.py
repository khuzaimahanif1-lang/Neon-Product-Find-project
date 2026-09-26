import os
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
from anyio import to_thread
from fastapi import HTTPException, Request, Response
from jwt import InvalidTokenError
from pwdlib import PasswordHash
from sqlalchemy import delete

from .config import BACKEND_DIR
from .models import AuthSession, User

COOKIE_NAME = "neonfind_session"
password_hasher = PasswordHash.recommended()
dummy_password_hash = password_hasher.hash(secrets.token_urlsafe(32))


def load_signing_secret(settings):
    if settings.jwt_secret:
        return settings.jwt_secret.get_secret_value()
    path = BACKEND_DIR / "data" / ".jwt-secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return path.read_text(encoding="utf-8").strip()
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        secret = secrets.token_urlsafe(48)
        file.write(secret)
    return secret


async def hash_password(password):
    return await to_thread.run_sync(password_hasher.hash, password)


async def verify_password(password, hashed):
    return await to_thread.run_sync(password_hasher.verify, password, hashed)


async def issue_session(user, session, request: Request, response: Response):
    settings = request.app.state.settings
    expires = datetime.now(UTC) + timedelta(minutes=settings.session_minutes)
    identifier = str(uuid4())
    session.add(AuthSession(id=identifier, user_id=user.id, expires_at=expires))
    await session.execute(delete(AuthSession).where(AuthSession.expires_at < datetime.now(UTC)))
    await session.commit()
    token = jwt.encode(
        {"sub": user.id, "jti": identifier, "exp": expires, "iat": datetime.now(UTC), "iss": "neonfind"},
        request.app.state.signing_secret,
        algorithm="HS256",
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.environment == "production",
        samesite="strict",
        max_age=settings.session_minutes * 60,
        path="/",
    )
    return {
        "user": {"id": user.id, "name": user.name, "email": user.email, "demo": False},
        "expiresIn": settings.session_minutes * 60,
    }


def decode_session(request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        return jwt.decode(
            token,
            request.app.state.signing_secret,
            algorithms=["HS256"],
            issuer="neonfind",
            options={"require": ["exp", "iat", "sub", "jti"]},
        )
    except InvalidTokenError:
        return None


async def current_user(request: Request, session, required=True):
    claims = decode_session(request)
    if claims:
        record = await session.get(AuthSession, claims["jti"])
        if (
            record
            and record.user_id == claims["sub"]
            and record.expires_at.replace(tzinfo=UTC) > datetime.now(UTC)
        ):
            user = await session.get(User, claims["sub"])
            if user:
                return user
    if required:
        raise HTTPException(401, "Please log in to continue.")
    return None
