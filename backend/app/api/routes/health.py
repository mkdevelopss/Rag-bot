from fastapi import APIRouter, Request

from app.schemas.api import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health(request: Request):
    settings = request.app.state.runtime_settings.get()
    return HealthResponse(
        status="ok",
        embedding_model=settings.embedding_model,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        llm_configured=bool(settings.llm_api_key),
    )
