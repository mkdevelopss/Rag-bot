from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import auth, chat, conversations, documents, health, settings as settings_routes
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services.database import Database
from app.services.authentication import AuthenticationService
from app.services.answer_cache import AnswerCache
from app.services.document_service import DocumentService
from app.services.embeddings import EmbeddingService
from app.services.rag_pipeline import LLMService, RagPipeline
from app.services.runtime_settings import RuntimeSettings
from app.services.settings_security import SettingsAuth
from app.services.vector_store import VectorStore


def create_app() -> FastAPI:
    configure_logging()
    base_settings = get_settings()
    database = Database(base_settings.resolve_path(base_settings.database_path))
    database.initialize()
    runtime_settings = RuntimeSettings(base_settings, database)
    authentication = AuthenticationService(database)
    embeddings = EmbeddingService(runtime_settings.get())
    vector_store = VectorStore(runtime_settings.get().resolve_path(runtime_settings.get().chroma_path))

    app = FastAPI(title="Document-grounded RAG Chatbot", version="1.0.0")
    app.state.database = database
    app.state.runtime_settings = runtime_settings
    app.state.authentication = authentication
    app.state.answer_cache = AnswerCache()
    app.state.settings_auth = SettingsAuth(runtime_settings.get().settings_password_hash)
    app.state.embeddings = embeddings
    app.state.vector_store = vector_store
    app.state.document_service = DocumentService(
        database, vector_store, embeddings, runtime_settings.get
    )
    app.state.rag_pipeline = RagPipeline(
        embeddings, vector_store, runtime_settings.get
    )
    app.state.llm_service = LLMService(runtime_settings.get)

    @app.middleware("http")
    async def require_browser_request_header(request, call_next):
        if (
            request.url.path.startswith("/api/")
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
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=runtime_settings.get().cors_origin_list,
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
    return app


app = create_app()
