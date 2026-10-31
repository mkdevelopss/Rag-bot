import re
import secrets
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.schemas.api import (
    AdminClaimRequest,
    AuthStatusResponse,
    ChangePasswordRequest,
    EmployeeCreateRequest,
    EmployeeCreateResponse,
    LoginRequest,
    UserActiveRequest,
    UserResponse,
)
from app.services.authentication import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_DAYS,
    SESSION_TTL_HOURS,
    get_current_user,
    hash_password,
    require_admin,
    require_user,
    verify_password,
)

router = APIRouter()


def _validate_email(email: str) -> str:
    normalized = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        raise HTTPException(status_code=422, detail="Enter a valid company email address.")
    return normalized


@router.get("/status", response_model=AuthStatusResponse)
def auth_status(request: Request, response: Response):
    user = request.app.state.authentication.current_user(
        request.cookies.get(SESSION_COOKIE_NAME)
    )
    if user is None:
        user, token = request.app.state.authentication.create_employee_session()
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            max_age=SESSION_TTL_DAYS * 24 * 60 * 60,
            httponly=True,
            secure=request.app.state.runtime_settings.get().auth_cookie_secure,
            samesite="strict",
            path="/",
        )
    return AuthStatusResponse(
        authenticated=True,
        setup_required=False,
        user=user,
    )


@router.post("/claim-admin", response_model=UserResponse)
def claim_admin(
    body: AdminClaimRequest,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
):
    database = request.app.state.database
    lock_key = "admin-bootstrap"
    if database.is_login_locked(lock_key):
        raise HTTPException(
            status_code=429,
            detail="Too many incorrect administrator keys. Wait one minute and try again.",
        )
    if database.count_admins():
        raise HTTPException(status_code=409, detail="An administrator has already claimed this workspace.")
    key_hash = request.app.state.runtime_settings.get().admin_bootstrap_key_hash
    if not key_hash:
        raise HTTPException(
            status_code=503,
            detail="Admin access is not initialized. Generate a key with `python -m app.cli generate-admin-key` in the backend folder, then restart the backend.",
        )
    if not verify_password(body.admin_key.get_secret_value(), key_hash):
        database.record_login_failure(lock_key)
        raise HTTPException(status_code=401, detail="The administrator key is incorrect.")
    promoted = database.promote_guest_to_admin(user["user_id"])
    if promoted is None:
        raise HTTPException(status_code=409, detail="This session can no longer claim administrator access.")
    database.clear_login_failures(lock_key)
    return promoted


@router.post("/login", response_model=AuthStatusResponse)
def login(body: LoginRequest, request: Request, response: Response):
    email = _validate_email(body.email)
    try:
        user, token = request.app.state.authentication.login(email, body.password)
    except PermissionError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_HOURS * 60 * 60,
        httponly=True,
        secure=request.app.state.runtime_settings.get().auth_cookie_secure,
        samesite="strict",
        path="/",
    )
    return AuthStatusResponse(
        authenticated=True,
        setup_required=False,
        user=user,
    )


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response):
    request.app.state.authentication.logout(
        request.cookies.get(SESSION_COOKIE_NAME)
    )
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        secure=request.app.state.runtime_settings.get().auth_cookie_secure,
        samesite="strict",
        path="/",
    )


@router.post("/change-password", response_model=UserResponse)
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
):
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_token:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    request.app.state.authentication.change_password(
        user["user_id"], session_token, body.new_password
    )
    updated = request.app.state.database.get_user_by_id(user["user_id"])
    return updated


@router.get(
    "/users", response_model=list[UserResponse], dependencies=[Depends(require_admin)]
)
def list_users(request: Request):
    return request.app.state.database.list_users()


@router.post(
    "/users",
    response_model=EmployeeCreateResponse,
    status_code=201,
    dependencies=[Depends(require_admin)],
)
def create_employee(
    body: EmployeeCreateRequest,
    request: Request,
):
    email = _validate_email(body.email)
    display_name = body.display_name.strip()
    if not display_name:
        raise HTTPException(status_code=422, detail="Employee name cannot be empty.")
    temporary_password = secrets.token_urlsafe(18)
    try:
        user = request.app.state.database.create_user(
            email=email,
            display_name=display_name,
            password_hash=hash_password(temporary_password),
            role="employee",
            must_change_password=True,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="An account with that email already exists.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return EmployeeCreateResponse(user=user, temporary_password=temporary_password)


@router.post(
    "/users/{user_id}/reset-password",
    response_model=EmployeeCreateResponse,
    dependencies=[Depends(require_admin)],
)
def reset_employee_password(
    user_id: str,
    request: Request,
):
    temporary_password = secrets.token_urlsafe(18)
    if not request.app.state.database.reset_user_password(
        user_id, hash_password(temporary_password)
    ):
        raise HTTPException(status_code=404, detail="Employee account not found.")
    user = request.app.state.database.get_user_by_id(user_id)
    return EmployeeCreateResponse(user=user, temporary_password=temporary_password)


@router.patch("/users/{user_id}/active", response_model=UserResponse)
def set_employee_active(
    user_id: str,
    body: UserActiveRequest,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
):
    if user_id == admin["user_id"]:
        raise HTTPException(status_code=422, detail="You cannot deactivate your own account.")
    user = request.app.state.database.get_user_by_id(user_id)
    if user is None or user["role"] != "employee":
        raise HTTPException(status_code=404, detail="Employee account not found.")
    request.app.state.database.set_user_active(user_id, body.is_active)
    return request.app.state.database.get_user_by_id(user_id)
