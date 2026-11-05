import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import auth, chat, conversations, documents, health, service_api, settings as settings_routes
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services.agent import Agent
from app.services.answer_cache import AnswerCache
from app.services.authentication import AuthenticationService
from app.services.chat_service import ChatService
from app.services.database import Database
from app.services.document_service import DocumentService
from app.services.embeddings import EmbeddingService
from app.services.llm import LLMClient
from app.services.rag_pipeline import RagPipeline
from app.services.rate_limit import RateLimiter
from app.services.runtime_settings import RuntimeSettings
from app.services.service_auth import parse_service_keys
from app.services.settings_security import SettingsAuth
from app.services.tools import AgentConfig
from app.services.vector_store import VectorStore

logger = logging.getLogger("app.requests")


def create_app() -> FastAPI:
    configure_logging()
    base_settings = get_settings()
    database = Database(base_settings.resolve_path(base_settings.database_path))
    database.initialize()
    runtime_settings = RuntimeSettings(base_settings, database)
    settings = runtime_settings.get()
    embeddings = EmbeddingService(settings)
    vector_store = VectorStore(settings.resolve_path(settings.chroma_path))
    rag_pipeline = RagPipeline(embeddings, vector_store, runtime_settings.get)
    llm = LLMClient(runtime_settings.get)
    agent = Agent(
        llm,
        rag_pipeline,
        database,
        AgentConfig(settings.resolve_path(settings.agent_config_path)),
        runtime_settings.get,
    )
    document_service = DocumentService(database, vector_store, embeddings, runtime_settings.get)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        document_service.resume_pending()
        yield

    app = FastAPI(title="Inkwell RAG Agent", version="2.0.0", lifespan=lifespan)
    app.state.database = database
    app.state.runtime_settings = runtime_settings
    app.state.authentication = AuthenticationService(database)
    app.state.answer_cache = AnswerCache()
    app.state.settings_auth = SettingsAuth(settings.settings_password_hash)
    app.state.embeddings = embeddings
    app.state.vector_store = vector_store
    app.state.document_service = document_service
    app.state.rag_pipeline = rag_pipeline
    app.state.llm_service = llm
    app.state.agent = agent
    app.state.service_keys = parse_service_keys(settings.service_api_keys)
    app.state.rate_limiter = RateLimiter(lambda: runtime_settings.get().chat_rate_limit_per_minute)
    app.state.chat_service = ChatService(
        database, rag_pipeline, llm, agent, app.state.answer_cache, runtime_settings.get
    )

    @app.middleware("http")
    async def security_and_logging(request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        started = time.perf_counter()
        is_service_api = request.url.path.startswith("/api/v1/")
        # Browser routes need a custom header (blocks cross-site form posts).
        # Service routes authenticate with a bearer key instead, so they're exempt.
        if (
            request.url.path.startswith("/api/")
            and not is_service_api
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.headers.get("x-inkwell-request") != "1"
        ):
            response = JSONResponse(
                status_code=403,
                content={"detail": "This request is missing its same-origin security header."},
            )
        else:
            response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        logger.info(
            "%s %s %s %.0fms id=%s",
            request.method, request.url.path, response.status_code,
            (time.perf_counter() - started) * 1000, request_id,
        )
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api/auth", tags=["authentication"])
    app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
    app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
    app.include_router(conversations.router, prefix="/api/conversations", tags=["conversations"])
    app.include_router(settings_routes.router, prefix="/api/settings", tags=["settings"])
    app.include_router(service_api.router, prefix="/api/v1/namespaces", tags=["service API"])
    return app


app = create_app()
