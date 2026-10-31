import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, Request

from app.services.database import Database


PASSWORD_ITERATIONS = 600_000
SESSION_TTL_HOURS = 12
SESSION_TTL_DAYS = 30
SESSION_COOKIE_NAME = "inkwell_session"


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = password_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


_DUMMY_PASSWORD_HASH = hash_password("Inkwell invalid-account timing guard")


class AuthenticationService:
    def __init__(self, database: Database):
        self.database = database

    def login(self, email: str, password: str) -> tuple[dict[str, Any], str]:
        normalized_email = email.strip().lower()
        if self.database.is_login_locked(normalized_email):
            raise PermissionError("Too many sign-in attempts. Wait one minute and try again.")

        user = self.database.get_user_by_email(normalized_email)
        candidate_hash = user["password_hash"] if user else _DUMMY_PASSWORD_HASH
        valid_password = verify_password(password, candidate_hash)
        if not user or not user["is_active"] or not valid_password:
            self.database.record_login_failure(normalized_email)
            raise ValueError("Email or password is incorrect.")

        self.database.clear_login_failures(normalized_email)
        session_token = secrets.token_urlsafe(32)
        session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
        self.database.create_session(session_hash, user["user_id"], expires_at)
        authenticated_user = self.database.get_user_by_id(user["user_id"])
        if authenticated_user is None:
            raise RuntimeError("The signed-in account could not be loaded.")
        return authenticated_user, session_token

    def create_employee_session(self) -> tuple[dict[str, Any], str]:
        user = self.database.create_guest_user()
        session_token = secrets.token_urlsafe(32)
        session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat()
        self.database.create_session(session_hash, user["user_id"], expires_at)
        return user, session_token

    def current_user(self, session_token: str | None) -> dict[str, Any] | None:
        if not session_token:
            return None
        session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
        return self.database.get_user_by_session(session_hash)

    def logout(self, session_token: str | None) -> None:
        if session_token:
            session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
            self.database.delete_session(session_hash)

    def change_password(self, user_id: str, session_token: str, password: str) -> None:
        session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
        self.database.update_user_password(user_id, hash_password(password), session_hash)


def get_current_user(request: Request) -> dict[str, Any]:
    user = request.app.state.authentication.current_user(
        request.cookies.get(SESSION_COOKIE_NAME)
    )
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return user


def require_user(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if user["must_change_password"]:
        raise HTTPException(
            status_code=403,
            detail="Change your temporary password before continuing.",
        )
    return user


def require_admin(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return user
