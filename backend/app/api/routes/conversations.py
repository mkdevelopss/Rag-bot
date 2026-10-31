from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.schemas.api import (
    ConversationCreate,
    ConversationPatch,
    ConversationResponse,
    ConversationSummary,
)
from app.services.authentication import require_user

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("", response_model=list[ConversationSummary])
def list_conversations(request: Request, user: dict[str, Any] = Depends(require_user)):
    return request.app.state.database.list_conversations(user["user_id"])


@router.post("", response_model=ConversationResponse, status_code=201)
def create_conversation(
    body: ConversationCreate,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
):
    conversation = request.app.state.database.create_conversation(
        user["user_id"], body.title or "New chat"
    )
    return conversation


@router.get("/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: str,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
):
    conversation = request.app.state.database.get_conversation(
        conversation_id, user["user_id"]
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation


@router.patch("/{conversation_id}", response_model=ConversationSummary)
def rename_conversation(
    conversation_id: str,
    body: ConversationPatch,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
):
    if not request.app.state.database.rename_conversation(
        conversation_id, user["user_id"], body.title.strip()
    ):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return next(
        item for item in request.app.state.database.list_conversations(user["user_id"])
        if item["conversation_id"] == conversation_id
    )


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: str,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
):
    if not request.app.state.database.delete_conversation(
        conversation_id, user["user_id"]
    ):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return Response(status_code=204)
