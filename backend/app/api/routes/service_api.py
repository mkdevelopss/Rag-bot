"""Server-to-server API: lets other apps use this RAG agent as a microservice.

Every route is scoped to a namespace (one per app or per tenant), and every
service key may only touch the namespaces its glob allows.
"""

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Path, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from app.api.routes.chat import check_rate_limit, sse_response
from app.api.routes.documents import validate_upload
from app.schemas.api import (
    NAMESPACE_PATTERN,
    ChatResponse,
    DocumentResponse,
    SearchHit,
    SearchRequest,
    ServiceChatRequest,
    TextDocumentRequest,
    UploadBatchResponse,
    UploadError,
)
from app.services.chat_service import ChatError
from app.services.document_service import DuplicateDocumentError
from app.services.service_auth import ServiceKey, require_service_key

router = APIRouter()


def scoped(
    request: Request,
    namespace: str = Path(..., pattern=NAMESPACE_PATTERN),
    key: ServiceKey = Depends(require_service_key),
) -> tuple[ServiceKey, str]:
    if not key.allows(namespace):
        raise HTTPException(status_code=403, detail=f"This key cannot access namespace '{namespace}'.")
    return key, namespace


def _owned_document(request: Request, namespace: str, document_id: str) -> dict[str, Any]:
    document = request.app.state.database.get_document(document_id)
    if document is None or document.get("namespace") != namespace:
        raise HTTPException(status_code=404, detail="Document not found.")
    return document


# ------------------------------------------------------------------ documents
@router.get("/{namespace}/documents", response_model=list[DocumentResponse])
def list_documents(request: Request, scope=Depends(scoped)):
    return request.app.state.database.list_documents(scope[1])


@router.post("/{namespace}/documents", response_model=UploadBatchResponse, status_code=201)
async def upload_documents(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    scope=Depends(scoped),
):
    namespace = scope[1]
    settings = request.app.state.runtime_settings.get()
    service = request.app.state.document_service
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    documents, errors = [], []
    for file in files:
        filename = file.filename or "uploaded-document"
        try:
            content = await file.read(max_bytes + 1)
            validate_upload(filename, file.content_type or "", len(content), max_bytes)
            document = await run_in_threadpool(service.register_upload, filename, content, namespace)
            background_tasks.add_task(service.process_document, document["document_id"])
            documents.append(DocumentResponse(**document))
        except (DuplicateDocumentError, ValueError) as exc:
            errors.append(UploadError(filename=filename, error=str(exc)))
        finally:
            await file.close()
    if not documents and errors:
        raise HTTPException(status_code=422, detail=[item.model_dump() for item in errors])
    return UploadBatchResponse(documents=documents, errors=errors)


@router.put("/{namespace}/documents/text", response_model=DocumentResponse, status_code=202)
async def upsert_text_document(
    body: TextDocumentRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    scope=Depends(scoped),
):
    """Add or replace a text record (FAQ, product, policy). Same external_id = replace."""
    service = request.app.state.document_service
    try:
        document = await run_in_threadpool(
            service.upsert_text, scope[1], body.title, body.text, body.external_id
        )
    except DuplicateDocumentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(service.process_document, document["document_id"])
    return document


@router.get("/{namespace}/documents/{document_id}", response_model=DocumentResponse)
def get_document(document_id: str, request: Request, scope=Depends(scoped)):
    return _owned_document(request, scope[1], document_id)


@router.delete("/{namespace}/documents/{document_id}", status_code=204)
def delete_document(document_id: str, request: Request, scope=Depends(scoped)):
    _owned_document(request, scope[1], document_id)
    request.app.state.document_service.delete(document_id)


@router.delete("/{namespace}/documents/by-external-id/{external_id}", status_code=204)
def delete_by_external_id(external_id: str, request: Request, scope=Depends(scoped)):
    document = request.app.state.database.find_document_by_external_id(scope[1], external_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    request.app.state.document_service.delete(document["document_id"])


# --------------------------------------------------------------------- search
@router.post("/{namespace}/search", response_model=list[SearchHit])
async def search(body: SearchRequest, request: Request, scope=Depends(scoped)):
    matches = await run_in_threadpool(
        request.app.state.rag_pipeline.search, body.query, scope[1], body.top_k
    )
    return [
        SearchHit(
            chunk_id=item["chunk_id"],
            document_id=item["document_id"],
            filename=item["filename"],
            page_number=item["page_number"],
            text=item["text"],
            similarity=round(float(item["similarity"]), 4),
            score=round(float(item["score"]), 6),
        )
        for item in matches
    ]


# ----------------------------------------------------------------------- chat
@router.post("/{namespace}/chat", response_model=ChatResponse)
async def chat(body: ServiceChatRequest, request: Request, scope=Depends(scoped)):
    key, namespace = scope
    check_rate_limit(request, f"service:{key.name}:{namespace}:{body.user_ref or '-'}")
    owner = await run_in_threadpool(request.app.state.database.get_or_create_service_user, key.name)
    service = request.app.state.chat_service
    try:
        turn = await run_in_threadpool(
            service.prepare,
            owner["user_id"],
            body.message,
            body.conversation_id,
            body.regenerate,
            namespace,
            body.user_ref or "",
            body.context,
        )
        if body.stream:
            return sse_response(service.events(turn))
        result = await run_in_threadpool(service.answer, turn)
    except ChatError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return ChatResponse(**result)


@router.get("/{namespace}/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    request: Request,
    user_ref: str = "",
    scope=Depends(scoped),
):
    key, namespace = scope
    owner = request.app.state.database.get_or_create_service_user(key.name)
    conversation = request.app.state.database.get_conversation(conversation_id, owner["user_id"])
    if (
        conversation is None
        or conversation.get("namespace") != namespace
        or (conversation.get("external_user") or "") != user_ref
    ):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation
