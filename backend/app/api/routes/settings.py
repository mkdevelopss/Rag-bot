from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.schemas.api import (
    ProviderSettingsRequest,
    SettingsPatch,
    SettingsResponse,
    SettingsUnlockRequest,
    SettingsUnlockResponse,
)
from app.services.authentication import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


def response_settings(settings) -> SettingsResponse:
    return SettingsResponse(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        top_k=settings.top_k,
        similarity_threshold=settings.similarity_threshold,
        max_context_chars=settings.max_context_chars,
        max_file_size_mb=settings.max_file_size_mb,
        embedding_model=settings.embedding_model,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        provider_configured=bool(settings.llm_api_key),
    )


@router.get("", response_model=SettingsResponse)
def get_settings(request: Request):
    return response_settings(request.app.state.runtime_settings.get())


@router.patch("", response_model=SettingsResponse)
def patch_settings(body: SettingsPatch, request: Request):
    updates = body.model_dump(exclude_unset=True, exclude_none=True)
    current = request.app.state.runtime_settings.get()
    if "chunk_size" in updates and "chunk_overlap" not in updates:
        if current.chunk_overlap >= updates["chunk_size"]:
            updates["chunk_overlap"] = min(current.chunk_overlap, updates["chunk_size"] - 1)
    try:
        updated = request.app.state.runtime_settings.update(updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return response_settings(updated)


@router.post("/unlock", response_model=SettingsUnlockResponse)
def unlock_settings(body: SettingsUnlockRequest, request: Request):
    try:
        token, expires_in = request.app.state.settings_auth.unlock(body.password)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return SettingsUnlockResponse(token=token, expires_in=expires_in)


@router.post("/provider", response_model=SettingsResponse)
def save_provider_settings(
    body: ProviderSettingsRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not token or not request.app.state.settings_auth.is_authorized(token):
        raise HTTPException(status_code=401, detail="Settings are locked. Unlock them with your password.")

    api_key = body.api_key.get_secret_value().strip() if body.api_key else None
    model = body.model.strip()
    if not model:
        raise HTTPException(status_code=422, detail="Model is required.")
    try:
        updated = request.app.state.runtime_settings.update_provider(
            body.provider,
            api_key,
            model,
        )
    except (OSError, ValueError) as exc:
        status_code = 422 if isinstance(exc, ValueError) else 500
        raise HTTPException(
            status_code=status_code,
            detail=f"Could not save provider settings to backend/.env: {exc}",
        ) from exc
    return response_settings(updated)


@router.post("/lock", status_code=204)
def lock_settings(
    request: Request,
    authorization: str | None = Header(default=None),
):
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if token:
        request.app.state.settings_auth.lock(token)
