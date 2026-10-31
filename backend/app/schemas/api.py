from datetime import datetime

from typing import Literal

from pydantic import BaseModel, Field, SecretStr


class HealthResponse(BaseModel):
    status: str
    embedding_model: str
    llm_provider: str
    llm_model: str
    llm_configured: bool


class DocumentResponse(BaseModel):
    document_id: str
    filename: str
    file_type: str
    size_bytes: int
    status: str
    chunk_count: int
    created_at: datetime
    error: str | None = None


class UploadError(BaseModel):
    filename: str
    error: str


class UploadBatchResponse(BaseModel):
    documents: list[DocumentResponse]
    errors: list[UploadError]


class SourceResponse(BaseModel):
    document_id: str
    filename: str
    page_number: int | None = None
    snippet: str
    similarity: float
    chunk_id: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    conversation_id: str | None = None
    regenerate: bool = False


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    sources: list[SourceResponse]


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    message_id: str
    role: str
    content: str
    sources: list[SourceResponse]
    created_at: datetime


class ConversationResponse(ConversationSummary):
    messages: list[MessageResponse]


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=120)


class ConversationPatch(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class SettingsResponse(BaseModel):
    chunk_size: int
    chunk_overlap: int
    top_k: int
    similarity_threshold: float
    max_context_chars: int
    max_file_size_mb: int
    embedding_model: str
    llm_provider: str
    llm_model: str
    provider_configured: bool


class SettingsPatch(BaseModel):
    chunk_size: int | None = Field(default=None, ge=100, le=5000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=1000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    similarity_threshold: float | None = Field(default=None, ge=-1, le=1)
    max_context_chars: int | None = Field(default=None, ge=1000, le=100000)


class SettingsUnlockRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class SettingsUnlockResponse(BaseModel):
    token: str
    expires_in: int


class ProviderSettingsRequest(BaseModel):
    provider: Literal["gemini", "groq"]
    api_key: SecretStr | None = Field(default=None, max_length=500)
    model: str = Field(min_length=1, max_length=160)


class UserResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: Literal["admin", "employee"]
    is_active: bool
    must_change_password: bool
    created_at: datetime
    last_login_at: datetime | None = None


class AuthStatusResponse(BaseModel):
    authenticated: bool
    setup_required: bool
    user: UserResponse | None = None


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class AdminClaimRequest(BaseModel):
    admin_key: SecretStr = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    new_password: str = Field(min_length=12, max_length=256)


class EmployeeCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=120)


class EmployeeCreateResponse(BaseModel):
    user: UserResponse
    temporary_password: str


class UserActiveRequest(BaseModel):
    is_active: bool
