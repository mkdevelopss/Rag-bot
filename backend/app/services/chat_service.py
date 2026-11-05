"""One chat flow shared by the web UI and the /api/v1 service API."""

import hashlib
import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.services.agent import Agent, AgentResult, ToolsNotSupported
from app.services.answer_cache import AnswerCache
from app.services.llm import LLMClient, LLMError
from app.services.rag_pipeline import (
    EMPTY_GENERATION_ANSWER,
    RagPipeline,
    is_greeting,
    no_context_answer,
    rewrite_query,
)

logger = logging.getLogger(__name__)
MAX_HISTORY_MESSAGE_CHARS = 4000
INTERRUPTED_SUFFIX = "\n\n_(response interrupted)_"


class ChatError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class ChatTurn:
    conversation_id: str
    owner_id: str
    namespace: str
    message: str
    history: list[dict[str, str]]
    regenerate: bool
    documents: list[dict[str, Any]]
    trusted_context: dict[str, Any] | None = None
    canned_answer: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class ChatService:
    def __init__(
        self,
        database,
        rag: RagPipeline,
        llm: LLMClient,
        agent: Agent,
        cache: AnswerCache,
        settings_provider,
    ):
        self.database = database
        self.rag = rag
        self.llm = llm
        self.agent = agent
        self.cache = cache
        self.settings_provider = settings_provider

    # ------------------------------------------------------------ prepare
    def prepare(
        self,
        owner_id: str,
        message: str,
        conversation_id: str | None,
        regenerate: bool = False,
        namespace: str = "default",
        external_user: str | None = None,
        trusted_context: dict[str, Any] | None = None,
    ) -> ChatTurn:
        settings: Settings = self.settings_provider()
        message = message.strip()
        if not message:
            raise ChatError(422, "Message cannot be empty.")
        if conversation_id:
            conversation = self.database.get_conversation(conversation_id, owner_id)
            if (
                conversation is None
                or conversation.get("namespace", "default") != namespace
                or (external_user is not None and conversation.get("external_user") != external_user)
            ):
                raise ChatError(404, "Conversation not found.")
            if regenerate:
                history = self.database.prepare_regeneration(conversation_id, owner_id, message)
                if history is None:
                    raise ChatError(409, "There is no matching answer to regenerate.")
            else:
                history = [
                    {"role": item["role"], "content": item["content"]}
                    for item in conversation["messages"]
                ]
        else:
            if regenerate:
                raise ChatError(422, "A conversation is required to regenerate an answer.")
            conversation_id = self.database.create_conversation(
                owner_id, namespace=namespace, external_user=external_user
            )["conversation_id"]
            history = []

        trimmed = [
            {"role": item["role"], "content": item["content"][:MAX_HISTORY_MESSAGE_CHARS]}
            for item in history[-settings.history_messages:]
        ] if settings.history_messages else []
        documents = self.database.list_documents(namespace)
        turn = ChatTurn(
            conversation_id=conversation_id,
            owner_id=owner_id,
            namespace=namespace,
            message=message,
            history=trimmed,
            regenerate=regenerate,
            documents=documents,
            trusted_context=trusted_context,
        )
        has_documents = any(doc["status"] == "ready" for doc in documents)
        if is_greeting(message) and not history:
            turn.canned_answer = no_context_answer(message, has_documents)
        elif not has_documents and not self.agent.has_app_tools(namespace):
            turn.canned_answer = no_context_answer(message, False)
        elif not self.llm.is_configured():
            raise ChatError(
                503,
                f"{settings.llm_provider.title()} is not configured. "
                f"Add {settings.llm_api_key_variable} to backend/.env.",
            )
        return turn

    # -------------------------------------------------------------- cache
    def _cache_key(self, turn: ChatTurn) -> str | None:
        settings: Settings = self.settings_provider()
        if (
            turn.history
            or turn.regenerate
            or turn.trusted_context
            or self.agent.has_app_tools(turn.namespace)
        ):
            return None  # personal or contextual answers are never shared
        material = {
            "namespace": turn.namespace,
            "question": " ".join(turn.message.lower().split()),
            "documents": sorted(
                (doc["document_id"], doc["status"], doc["updated_at"], doc["chunk_count"])
                for doc in turn.documents
            ),
            "model": (settings.llm_provider, settings.llm_model, settings.agent_enabled),
            "retrieval": (
                settings.embedding_model, settings.top_k, settings.similarity_threshold,
                settings.max_context_chars, settings.chunk_size, settings.chunk_overlap,
                settings.hybrid_search,
            ),
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()

    # ------------------------------------------------------------- answer
    def _plain_rag(self, turn: ChatTurn, state: dict[str, Any]) -> Iterator[tuple[str, Any]]:
        settings: Settings = self.settings_provider()
        query = turn.message
        if settings.query_rewrite and turn.history:
            yield "status", {"tool": "rewrite", "message": "Understanding your question"}
            query = rewrite_query(self.llm, turn.history, turn.message)
        sources, context = self.rag.retrieve(query, turn.namespace, turn.documents)
        state["sources"] = sources
        yield "sources", sources
        if not sources:
            text = no_context_answer(turn.message, True)
            state["parts"].append(text)
            yield "delta", text
            return
        for part in self.llm.stream_text(self.rag.build_messages(turn.message, context, turn.history)):
            state["parts"].append(part)
            yield "delta", part

    def events(self, turn: ChatTurn) -> Iterator[tuple[str, dict[str, Any]]]:
        settings: Settings = self.settings_provider()
        state: dict[str, Any] = {"parts": [], "sources": []}
        finished = False
        yield "meta", {"conversation_id": turn.conversation_id}
        try:
            cache_key = None if turn.canned_answer else self._cache_key(turn)
            cached = self.cache.get(cache_key) if cache_key else None
            if turn.canned_answer is not None:
                state["parts"].append(turn.canned_answer)
                yield "sources", {"sources": []}
                yield "delta", {"text": turn.canned_answer}
            elif cached is not None:
                state["parts"].append(cached[0])
                state["sources"] = cached[1]
                yield "sources", {"sources": cached[1]}
                yield "delta", {"text": cached[0]}
            else:
                if settings.agent_enabled:
                    result = AgentResult()
                    try:
                        stream = self.agent.run(
                            turn.message, turn.history, turn.namespace, turn.trusted_context, result
                        )
                        for kind, payload in stream:
                            if kind == "delta":
                                state["parts"].append(payload)
                            elif kind == "sources":
                                state["sources"] = payload
                            yield kind, self._payload(kind, payload)
                        turn.tool_calls = result.tool_calls
                        state["sources"] = result.sources
                    except ToolsNotSupported:
                        logger.warning("Model rejected tool calling; falling back to plain RAG")
                        for kind, payload in self._plain_rag(turn, state):
                            yield kind, self._payload(kind, payload)
                else:
                    for kind, payload in self._plain_rag(turn, state):
                        yield kind, self._payload(kind, payload)

            answer = "".join(state["parts"]).strip()
            if not answer:
                answer = EMPTY_GENERATION_ANSWER
                yield "delta", {"text": answer}
            elif cache_key and cached is None and turn.canned_answer is None:
                self.cache.set(cache_key, (answer, state["sources"]))
            self._persist(turn, answer, state["sources"])
            finished = True
            yield "done", {"conversation_id": turn.conversation_id}
        except GeneratorExit:
            # Client disconnected mid-answer: keep what was generated.
            partial = "".join(state["parts"]).strip()
            if partial and not finished:
                try:
                    self._persist(turn, partial + INTERRUPTED_SUFFIX, state["sources"])
                except Exception:
                    logger.exception("Could not save interrupted answer")
            raise
        except LLMError as exc:
            yield "error", {"message": str(exc), "status_code": exc.status_code}
        except Exception as exc:
            logger.exception("Chat failed")
            yield "error", {"message": f"Something went wrong: {exc}", "status_code": 500}

    @staticmethod
    def _payload(kind: str, payload: Any) -> dict[str, Any]:
        if kind == "delta":
            return {"text": payload}
        if kind == "sources":
            return {"sources": payload}
        return payload

    def _persist(self, turn: ChatTurn, answer: str, sources: list[dict[str, Any]]) -> None:
        if turn.regenerate:
            self.database.replace_last_assistant(
                turn.conversation_id, turn.owner_id, turn.message, answer, sources
            )
        else:
            self.database.add_exchange(
                turn.conversation_id, turn.owner_id, turn.message, answer, sources
            )

    def answer(self, turn: ChatTurn) -> dict[str, Any]:
        parts, sources, steps = [], [], []
        for kind, data in self.events(turn):
            if kind == "delta":
                parts.append(data["text"])
            elif kind == "sources":
                sources = data["sources"]
            elif kind == "status":
                steps.append(data)
            elif kind == "error":
                raise ChatError(data.get("status_code", 502), data["message"])
        return {
            "conversation_id": turn.conversation_id,
            "answer": "".join(parts).strip(),
            "sources": sources,
            "steps": steps,
        }
