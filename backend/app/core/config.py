from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[2]

# OpenAI-compatible endpoints. Every supported provider speaks the same
# chat-completions + tool-calling protocol, so one client handles all of them.
PROVIDER_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "openai": None,  # SDK default
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM provider -------------------------------------------------------
    llm_provider: Literal["gemini", "groq", "openai"] = "groq"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"
    # Optional override, e.g. OpenRouter, Ollama or vLLM (any OpenAI-compatible URL).
    llm_base_url: str = ""
    llm_temperature: float = Field(default=0.2, ge=0, le=2)
    llm_timeout_seconds: float = Field(default=60, ge=5, le=600)
    llm_max_retries: int = Field(default=2, ge=0, le=6)

    # --- Agent --------------------------------------------------------------
    agent_enabled: bool = True
    agent_max_steps: int = Field(default=4, ge=1, le=10)
    agent_config_path: str = "agent_config.json"
    query_rewrite: bool = True
    history_messages: int = Field(default=12, ge=0, le=50)

    # --- Security -----------------------------------------------------------
    settings_password_hash: str = ""
    admin_bootstrap_key_hash: str = ""
    auth_cookie_secure: bool = False
    # Comma-separated "name:sha256_of_key:namespace_glob" entries.
    # Generate with: python -m app.cli generate-service-key --name marketbid --namespaces marketbid
    service_api_keys: str = ""
    chat_rate_limit_per_minute: int = Field(default=30, ge=1, le=10000)

    # --- Retrieval ----------------------------------------------------------
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Some models (e5, bge) expect prefixes such as "query: " / "passage: ".
    embedding_query_prefix: str = ""
    embedding_passage_prefix: str = ""
    chunk_size: int = Field(default=800, ge=100, le=5000)
    chunk_overlap: int = Field(default=150, ge=0, le=1000)
    top_k: int = Field(default=5, ge=1, le=20)
    similarity_threshold: float = Field(default=0.25, ge=-1, le=1)
    hybrid_search: bool = True
    max_context_chars: int = Field(default=12000, ge=1000, le=100000)
    max_file_size_mb: int = Field(default=20, ge=1, le=200)

    # --- Infrastructure -----------------------------------------------------
    cors_origins: str = "http://localhost:5173"
    database_path: str = "data/rag_chatbot.sqlite3"
    chroma_path: str = "data/chroma_db"
    upload_path: str = "data/uploads"

    @field_validator("chunk_overlap")
    @classmethod
    def overlap_must_be_less_than_chunk_size(cls, value: int, info: ValidationInfo) -> int:
        chunk_size = info.data.get("chunk_size", 800)
        if value >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return value

    def resolve_path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else BACKEND_DIR / path

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def llm_model(self) -> str:
        return {
            "gemini": self.gemini_model,
            "groq": self.groq_model,
            "openai": self.openai_model,
        }[self.llm_provider]

    @property
    def llm_api_key(self) -> str:
        return {
            "gemini": self.gemini_api_key,
            "groq": self.groq_api_key,
            "openai": self.openai_api_key,
        }[self.llm_provider]

    @property
    def resolved_llm_base_url(self) -> str | None:
        return self.llm_base_url.strip() or PROVIDER_BASE_URLS[self.llm_provider]

    @property
    def llm_api_key_variable(self) -> str:
        return f"{self.llm_provider.upper()}_API_KEY"


@lru_cache
def get_settings() -> Settings:
    return Settings()
