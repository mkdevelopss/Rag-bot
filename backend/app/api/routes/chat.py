import hashlib
import json
import logging
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.schemas.api import ChatRequest, ChatResponse, SourceResponse
from app.services.rag_pipeline import (
    EMPTY_GENERATION_ANSWER,
    is_greeting,
    no_context_answer,
)
from app.services.authentication import require_user

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_user)])


def _conversation_context(
    database, conversation_id: str, user_id: str
) -> list[dict[str, str]]:
    conversation = database.get_conversation(conversation_id, user_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return [
        {"role": message["role"], "content": message["content"]}
        for message in conversation["messages"]
    ]


def _prepare(request: Request, body: ChatRequest, user: dict[str, Any]):
    database = request.app.state.database
    user_id = user["user_id"]
    if body.conversation_id:
        conversation_id = body.conversation_id
        if body.regenerate:
            history = database.prepare_regeneration(conversation_id, user_id, body.message)
            if history is None:
                raise HTTPException(status_code=409, detail="There is no matching answer to regenerate.")
        else:
            history = _conversation_context(database, conversation_id, user_id)
    else:
        if body.regenerate:
            raise HTTPException(status_code=422, detail="A conversation is required to regenerate an answer.")
        conversation = database.create_conversation(user_id)
        conversation_id = conversation["conversation_id"]
        history = []
    documents = database.list_documents()
    has_documents = any(document["status"] == "ready" for document in documents)
    if is_greeting(body.message):
        sources, context = [], ""
    else:
        sources, context = request.app.state.rag_pipeline.retrieve(body.message)
    if not body.regenerate:
        database.add_message(conversation_id, user_id, "user", body.message)
    return conversation_id, history, sources, context, has_documents, documents


def _answer_cache_key(
    request: Request,
    user: dict[str, Any],
    message: str,
    history: list[dict[str, str]],
    sources: list[dict[str, Any]],
    context: str,
    documents: list[dict[str, Any]],
) -> str:
    settings = request.app.state.runtime_settings.get()
    material = {
        "user_id": user["user_id"],
        "question": message,
        "history": history,
        "context": context,
        "sources": [
            (source["document_id"], source["chunk_id"]) for source in sources
        ],
        "documents": [
            (
                document["document_id"],
                document["filename"],
                document["status"],
                document["chunk_count"],
                document["created_at"],
                document["updated_at"],
            )
            for document in documents
        ],
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "provider_key_digest": hashlib.sha256(
            settings.llm_api_key.encode("utf-8")
        ).hexdigest(),
        "embedding_model": settings.embedding_model,
        "retrieval": (
            settings.top_k,
            settings.similarity_threshold,
            settings.max_context_chars,
            settings.chunk_size,
            settings.chunk_overlap,
        ),
    }
    encoded = json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _generate_or_reuse_answer(
    request: Request,
    cache_key: str,
    message: str,
    context: str,
    history: list[dict[str, str]],
) -> str:
    cache = request.app.state.answer_cache
    answer = cache.get(cache_key)
    if answer is not None:
        return answer
    answer = "".join(request.app.state.llm_service.stream(message, context, history))
    if answer.strip():
        cache.set(cache_key, answer)
    return answer


@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
):
    try:
        conversation_id, history, sources, context, has_documents, documents = await run_in_threadpool(
            _prepare, request, body, user
        )
        if sources:
            cache_key = _answer_cache_key(
                request, user, body.message, history, sources, context, documents
            )
            chunks = await run_in_threadpool(
                lambda: [_generate_or_reuse_answer(
                    request, cache_key, body.message, context, history
                )]
            )
            answer = "".join(chunks)
        else:
            answer = no_context_answer(body.message, has_documents)
        if sources and not answer.strip():
            answer = EMPTY_GENERATION_ANSWER
        if body.regenerate:
            await run_in_threadpool(
                request.app.state.database.replace_last_assistant,
                conversation_id,
                user["user_id"],
                body.message,
                answer,
                sources,
            )
        else:
            await run_in_threadpool(
                request.app.state.database.add_message,
                conversation_id,
                user["user_id"],
                "assistant",
                answer,
                sources,
            )
        return ChatResponse(
            conversation_id=conversation_id,
            answer=answer,
            sources=[SourceResponse(**source) for source in sources],
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Chat request failed")
        message = str(exc)
        if "Gemini is not configured" in message:
            raise HTTPException(status_code=503, detail=message) from exc
        raise HTTPException(status_code=502, detail=message) from exc


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/stream")
async def stream_chat(
    body: ChatRequest,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
):
    try:
        conversation_id, history, sources, context, has_documents, documents = await run_in_threadpool(
            _prepare, request, body, user
        )
        if sources and not request.app.state.llm_service.is_configured():
            settings = request.app.state.runtime_settings.get()
            variable = "GEMINI_API_KEY" if settings.llm_provider == "gemini" else "GROQ_API_KEY"
            raise HTTPException(
                status_code=503,
                detail=(
                    f"{settings.llm_provider.title()} is not configured. "
                    f"Add {variable} to backend/.env."
                ),
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Could not prepare chat stream")
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    def generate() -> Iterator[str]:
        answer_parts = []
        yield _sse("meta", {"conversation_id": conversation_id})
        yield _sse("sources", {"sources": sources})
        try:
            if not sources:
                answer = no_context_answer(body.message, has_documents)
                answer_parts.append(answer)
                yield _sse("delta", {"text": answer})
            else:
                cache_key = _answer_cache_key(
                    request, user, body.message, history, sources, context, documents
                )
                cached_answer = request.app.state.answer_cache.get(cache_key)
                if cached_answer is not None:
                    answer_parts.append(cached_answer)
                    yield _sse("delta", {"text": cached_answer})
                else:
                    for part in request.app.state.llm_service.stream(body.message, context, history):
                        answer_parts.append(part)
                        yield _sse("delta", {"text": part})
            answer = "".join(answer_parts)
            if sources and not answer.strip():
                answer = EMPTY_GENERATION_ANSWER
                yield _sse("delta", {"text": answer})
            elif sources:
                request.app.state.answer_cache.set(cache_key, answer)
            if body.regenerate:
                request.app.state.database.replace_last_assistant(
                    conversation_id, user["user_id"], body.message, answer, sources
                )
            else:
                request.app.state.database.add_message(
                    conversation_id, user["user_id"], "assistant", answer, sources
                )
            yield _sse("done", {"conversation_id": conversation_id})
        except Exception as exc:
            logger.exception("Chat stream failed")
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
