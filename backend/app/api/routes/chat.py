import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.schemas.api import ChatRequest, ChatResponse
from app.services.authentication import require_user
from app.services.chat_service import ChatError, ChatService

router = APIRouter(dependencies=[Depends(require_user)])


def sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def sse_response(events: Iterator[tuple[str, dict[str, Any]]]) -> StreamingResponse:
    return StreamingResponse(
        (sse(kind, data) for kind, data in events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def check_rate_limit(request: Request, key: str) -> None:
    if not request.app.state.rate_limiter.allow(key):
        raise HTTPException(status_code=429, detail="Too many messages. Wait a minute and try again.")


async def _prepare(request: Request, body: ChatRequest, user: dict[str, Any]):
    check_rate_limit(request, f"user:{user['user_id']}")
    service: ChatService = request.app.state.chat_service
    try:
        return await run_in_threadpool(
            service.prepare, user["user_id"], body.message, body.conversation_id, body.regenerate
        )
    except ChatError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request, user: dict[str, Any] = Depends(require_user)):
    turn = await _prepare(request, body, user)
    try:
        result = await run_in_threadpool(request.app.state.chat_service.answer, turn)
    except ChatError as exc:
        raise HTTPException(status_code=exc.status_code if exc.status_code >= 400 else 502, detail=exc.detail) from exc
    return ChatResponse(**result)


@router.post("/stream")
async def stream_chat(body: ChatRequest, request: Request, user: dict[str, Any] = Depends(require_user)):
    turn = await _prepare(request, body, user)
    return sse_response(request.app.state.chat_service.events(turn))
