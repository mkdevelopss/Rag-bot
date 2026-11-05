from io import BytesIO
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "app.sqlite3"))
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "chroma"))
    monkeypatch.setenv("UPLOAD_PATH", str(tmp_path / "uploads"))
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "")
    from app.services.settings_security import hash_settings_password
    from app.services.authentication import hash_password

    monkeypatch.setenv(
        "SETTINGS_PASSWORD_HASH",
        hash_settings_password("test-password", b"pytest-settings-salt"),
    )
    monkeypatch.setenv(
        "ADMIN_BOOTSTRAP_KEY_HASH",
        hash_password("test-admin-bootstrap-key", b"pytest-admin-key-salt"),
    )
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app(), headers={"X-Inkwell-Request": "1"}) as test_client:
        test_client.app.state.runtime_settings.env_path = tmp_path / "test.env"
        test_client.app.state.database.create_user(
            email="admin@company.test",
            display_name="Company Admin",
            password_hash=hash_password("test-admin-password"),
            role="admin",
            must_change_password=False,
        )
        login = test_client.post(
            "/api/auth/login",
            json={"email": "admin@company.test", "password": "test-admin-password"},
        )
        assert login.status_code == 200
        yield test_client
    get_settings.cache_clear()


def test_health_and_settings_endpoints(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["llm_configured"] is False
    assert health.json()["llm_provider"] == "groq"
    assert health.json()["llm_model"] == "openai/gpt-oss-120b"
    assert "api_key" not in str(client.get("/api/settings").json()).lower()

    response = client.patch("/api/settings", json={"chunk_size": 400, "chunk_overlap": 80})
    assert response.status_code == 200
    assert response.json()["chunk_size"] == 400
    assert response.json()["chunk_overlap"] == 80
    assert client.get("/api/settings").json()["chunk_size"] == 400


def test_employee_accounts_require_admin_and_change_temporary_password(client):
    status = client.get("/api/auth/status")
    assert status.status_code == 200
    assert status.json()["authenticated"] is True
    assert status.json()["user"]["role"] == "admin"
    assert "password_hash" not in status.text
    assert "test-admin-password" not in status.text

    created = client.post(
        "/api/auth/users",
        json={"email": "employee@company.test", "display_name": "Company Employee"},
    )
    assert created.status_code == 201
    employee = created.json()["user"]
    temporary_password = created.json()["temporary_password"]
    assert employee["must_change_password"] is True
    assert employee["role"] == "employee"
    assert "password_hash" not in created.text

    assert client.post(
        "/api/auth/users",
        json={"email": "EMPLOYEE@company.test", "display_name": "Duplicate"},
    ).status_code == 409

    with TestClient(client.app, headers={"X-Inkwell-Request": "1"}) as employee_client:
        login = employee_client.post(
            "/api/auth/login",
            json={"email": "employee@company.test", "password": temporary_password},
        )
        assert login.status_code == 200
        assert login.json()["user"]["must_change_password"] is True
        assert "HttpOnly" in login.headers["set-cookie"]
        assert "SameSite=strict" in login.headers["set-cookie"]

        assert employee_client.get("/api/documents").status_code == 403
        changed = employee_client.post(
            "/api/auth/change-password",
            json={"new_password": "employee-password-2026"},
        )
        assert changed.status_code == 200
        assert changed.json()["must_change_password"] is False
        assert employee_client.get("/api/documents").status_code == 200
        assert employee_client.get("/api/settings").status_code == 403
        assert employee_client.get("/api/auth/users").status_code == 403
        assert employee_client.post(
            "/api/auth/users",
            json={"email": "other@company.test", "display_name": "Other"},
        ).status_code == 403
        assert employee_client.post(
            "/api/documents/upload",
            files={"files": ("secret.txt", b"private upload", "text/plain")},
        ).status_code == 403

        admin_conversation = client.post("/api/conversations", json={}).json()
        employee_conversation = employee_client.post("/api/conversations", json={}).json()
        assert employee_client.get(
            f"/api/conversations/{admin_conversation['conversation_id']}"
        ).status_code == 404
        assert client.get(
            f"/api/conversations/{employee_conversation['conversation_id']}"
        ).status_code == 404
        assert len(employee_client.get("/api/conversations").json()) == 1

        deactivated = client.patch(
            f"/api/auth/users/{employee['user_id']}/active",
            json={"is_active": False},
        )
        assert deactivated.status_code == 200
        assert employee_client.get("/api/documents").status_code == 401


def test_anonymous_sessions_can_use_employee_workspace(client):
    with TestClient(client.app) as missing_header:
        rejected = missing_header.post(
            "/api/auth/login",
            json={"email": "admin@company.test", "password": "test-admin-password"},
        )
        assert rejected.status_code == 403

    with TestClient(client.app, headers={"X-Inkwell-Request": "1"}) as anonymous:
        status = anonymous.get("/api/auth/status")
        assert status.json()["authenticated"] is True
        assert status.json()["user"]["role"] == "employee"
        assert "HttpOnly" in status.headers["set-cookie"]
        assert anonymous.get("/api/documents").status_code == 200
        assert anonymous.get("/api/conversations").status_code == 200
        assert anonymous.get("/api/settings").status_code == 403
        assert anonymous.post("/api/chat/stream", json={"message": "hello"}).status_code == 200


def test_admin_bootstrap_key_is_single_use_and_histories_stay_private(tmp_path, monkeypatch):
    from app.core.config import get_settings
    from app.main import create_app
    from app.services.authentication import hash_password

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "bootstrap.sqlite3"))
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "chroma"))
    monkeypatch.setenv("UPLOAD_PATH", str(tmp_path / "uploads"))
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv(
        "ADMIN_BOOTSTRAP_KEY_HASH",
        hash_password("one-time-admin-key", b"pytest-bootstrap-salt"),
    )
    get_settings.cache_clear()
    try:
        with TestClient(create_app(), headers={"X-Inkwell-Request": "1"}) as browser:
            status = browser.get("/api/auth/status")
            assert status.json()["user"]["role"] == "employee"
            original_user_id = status.json()["user"]["user_id"]
            conversation = browser.post("/api/conversations", json={}).json()

            wrong_key = browser.post(
                "/api/auth/claim-admin", json={"admin_key": "incorrect-key"}
            )
            assert wrong_key.status_code == 401
            claimed = browser.post(
                "/api/auth/claim-admin", json={"admin_key": "one-time-admin-key"}
            )
            assert claimed.status_code == 200
            assert claimed.json()["role"] == "admin"
            assert claimed.json()["user_id"] == original_user_id
            assert browser.get(
                f"/api/conversations/{conversation['conversation_id']}"
            ).status_code == 200

            with TestClient(browser.app, headers={"X-Inkwell-Request": "1"}) as other_browser:
                other_status = other_browser.get("/api/auth/status")
                assert other_status.json()["user"]["role"] == "employee"
                assert other_browser.get(
                    f"/api/conversations/{conversation['conversation_id']}"
                ).status_code == 404
                assert other_browser.post(
                    "/api/auth/claim-admin",
                    json={"admin_key": "one-time-admin-key"},
                ).status_code == 409
    finally:
        get_settings.cache_clear()


def test_existing_conversations_are_assigned_to_the_first_admin(tmp_path):
    from app.services.database import Database, utc_now

    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE conversations (
            conversation_id TEXT PRIMARY KEY, title TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
        )
        connection.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?)",
            ("legacy-conversation", "Existing chat", utc_now(), utc_now()),
        )

    database = Database(path)
    database.initialize()
    admin = database.create_user(
        email="first-admin@company.test",
        display_name="First Admin",
        password_hash="test-hash",
        role="admin",
        must_change_password=False,
    )
    assert database.get_conversation(
        "legacy-conversation", admin["user_id"]
    )["title"] == "Existing chat"
    assert database.get_conversation("legacy-conversation", "another-user") is None


def test_local_first_admin_setup_creates_account_and_settings_lock(tmp_path, monkeypatch):
    from app.core.config import get_settings
    from app.services import runtime_settings
    from app.services.authentication import verify_password
    from app.cli import create_admin
    from app.services.database import Database

    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "admin.sqlite3"))
    monkeypatch.setattr(runtime_settings, "BACKEND_DIR", tmp_path)
    answers = iter(["admin@company.test", "Initial Admin"])
    passwords = iter(["strong-admin-password-2026", "strong-admin-password-2026"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda _prompt: next(passwords))

    try:
        assert create_admin() == 0
        database = Database(tmp_path / "admin.sqlite3")
        user = database.get_user_by_email("admin@company.test")
        assert user["role"] == "admin"
        assert verify_password("strong-admin-password-2026", user["password_hash"])
        settings_line = next(
            line for line in (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
            if line.startswith("SETTINGS_PASSWORD_HASH=")
        )
        settings_hash = settings_line.split("=", 1)[1].strip('"')
        assert verify_password("strong-admin-password-2026", settings_hash)
    finally:
        get_settings.cache_clear()


def test_conversation_create_rename_and_delete(client):
    created = client.post("/api/conversations", json={})
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]
    renamed = client.patch(
        f"/api/conversations/{conversation_id}",
        json={"title": "Project notes"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Project notes"
    assert client.delete(f"/api/conversations/{conversation_id}").status_code == 204
    assert client.get(f"/api/conversations/{conversation_id}").status_code == 404


def test_provider_key_changes_require_password_and_key_is_never_returned(client):
    wrong = client.post("/api/settings/unlock", json={"password": "not-the-password"})
    assert wrong.status_code == 401

    unlocked = client.post("/api/settings/unlock", json={"password": "test-password"})
    assert unlocked.status_code == 200
    token = unlocked.json()["token"]
    assert unlocked.json()["expires_in"] == 900

    missing_key = client.post(
        "/api/settings/provider",
        json={"provider": "groq", "model": "llama-test-model"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert missing_key.status_code == 422

    body = {
        "provider": "groq",
        "api_key": "test-provider-key-that-must-not-leak",
        "model": "llama-test-model",
    }
    unauthorized = client.post("/api/settings/provider", json=body)
    assert unauthorized.status_code == 401

    saved = client.post(
        "/api/settings/provider",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert saved.status_code == 200
    assert saved.json()["provider_configured"] is True
    assert saved.json()["llm_model"] == "llama-test-model"
    assert "test-provider-key-that-must-not-leak" not in saved.text
    settings_response = client.get("/api/settings")
    assert "test-provider-key-that-must-not-leak" not in settings_response.text
    assert "api_key" not in settings_response.text.lower()

    model_only = client.post(
        "/api/settings/provider",
        json={"provider": "groq", "model": "llama-model-only"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert model_only.status_code == 200
    assert model_only.json()["llm_model"] == "llama-model-only"
    assert "test-provider-key-that-must-not-leak" not in model_only.text

    env_file = client.app.state.runtime_settings.env_path
    contents = env_file.read_text(encoding="utf-8")
    assert 'GROQ_API_KEY="test-provider-key-that-must-not-leak"' in contents
    assert "GROQ_MODEL" in contents
    assert client.get("/api/settings").json()["provider_configured"] is True

    assert client.post(
        "/api/settings/lock",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 204
    locked_save = client.post(
        "/api/settings/provider",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert locked_save.status_code == 401


def test_upload_rejects_unsupported_and_empty_documents(client):
    unsupported = client.post(
        "/api/documents/upload",
        files={"files": ("payload.exe", b"no", "application/octet-stream")},
    )
    assert unsupported.status_code == 422
    empty = client.post(
        "/api/documents/upload",
        files={"files": ("empty.txt", b"", "text/plain")},
    )
    assert empty.status_code == 422


def test_pdf_upload_is_accepted_and_indexed(client, monkeypatch):
    database = client.app.state.database

    def finish_without_model(document_id):
        database.update_document(document_id, status="ready", chunk_count=1, error=None)

    monkeypatch.setattr(client.app.state.document_service, "process_document", finish_without_model)
    pdf_content = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(pdf_content)

    response = client.post(
        "/api/documents/upload",
        files={"files": ("notes.pdf", pdf_content.getvalue(), "application/pdf")},
    )

    assert response.status_code == 201
    document = response.json()["documents"][0]
    assert document["filename"] == "notes.pdf"
    assert document["file_type"] == "PDF"
    assert client.get(f"/api/documents/{document['document_id']}").json()["status"] == "ready"


def test_document_upload_status_duplicate_and_delete(client, monkeypatch):
    service = client.app.state.document_service
    database = client.app.state.database

    def finish_without_model(document_id):
        database.update_document(document_id, status="ready", chunk_count=1, error=None)

    monkeypatch.setattr(service, "process_document", finish_without_model)
    response = client.post(
        "/api/documents/upload",
        files={"files": ("notes.txt", b"Useful project notes.", "text/plain")},
    )
    assert response.status_code == 201
    document = response.json()["documents"][0]
    assert document["status"] == "processing"
    document_id = document["document_id"]
    assert client.get(f"/api/documents/{document_id}").json()["status"] == "ready"

    duplicate = client.post(
        "/api/documents/upload",
        files={"files": ("copy.txt", b"Useful project notes.", "text/plain")},
    )
    assert duplicate.status_code == 422
    assert client.delete(f"/api/documents/{document_id}").status_code == 204
    assert client.get(f"/api/documents/{document_id}").status_code == 404


# ---------------------------------------------------------------- chat + agent
from types import SimpleNamespace

from app.services.llm import ToolCall


class ScriptedLLM:
    """Plays back scripted rounds. Each round is a list of ("text", str) / ("tool_calls", [...])."""

    def __init__(self, rounds, configured=True):
        self.rounds = list(rounds)
        self.requests = []
        self.configured = configured

    def is_configured(self):
        return self.configured

    def stream_chat(self, messages, tools=None):
        self.requests.append({"messages": messages, "tools": tools})
        yield from self.rounds.pop(0)

    def stream_text(self, messages):
        for kind, payload in self.stream_chat(messages):
            if kind == "text":
                yield payload

    def complete(self, messages, max_tokens=1024):
        self.requests.append({"messages": messages, "complete": True})
        return "standalone query"


def _ready_document(client, document_id="doc-1", namespace="default"):
    from app.services.database import utc_now

    client.app.state.database.create_document({
        "document_id": document_id, "filename": "policy.txt", "file_type": "TXT",
        "size_bytes": 10, "content_hash": f"hash-{document_id}", "storage_path": "unused",
        "status": "ready", "chunk_count": 1, "created_at": utc_now(), "error": None,
        "namespace": namespace,
    })


def _use_llm(client, llm):
    client.app.state.llm_service = llm
    client.app.state.agent.llm = llm
    client.app.state.chat_service.llm = llm
    client.app.state.chat_service.agent.llm = llm


def _fake_search(client, monkeypatch, text="Refunds are allowed within 7 days."):
    match = {
        "chunk_id": "doc-1_0", "text": text, "document_id": "doc-1", "filename": "policy.txt",
        "page_number": 2, "chunk_index": 0, "similarity": 0.9, "score": 0.03,
    }
    calls = []

    def search(query, namespace="default", top_k=None, document_ids=None, apply_threshold=True):
        calls.append((query, namespace))
        return [match]

    monkeypatch.setattr(client.app.state.rag_pipeline, "search", search)
    return calls


def _events(text):
    import json as _json

    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], _json.loads(lines["data"])))
    return events


def test_agent_searches_cites_and_saves_the_exchange(client, monkeypatch):
    _ready_document(client)
    searches = _fake_search(client, monkeypatch)
    llm = ScriptedLLM([
        [("tool_calls", [ToolCall("c1", "search_knowledge_base", '{"query": "refund policy"}')])],
        [("text", "You can get a refund within 7 days [1].")],
    ])
    _use_llm(client, llm)
    response = client.post("/api/chat/stream", json={"message": "Can I get a refund?"})
    events = _events(response.text)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "meta" and kinds[-1] == "done"
    assert ("status", {"tool": "search_knowledge_base", "message": "Searching: refund policy"}) in events
    final_sources = [data for kind, data in events if kind == "sources"][-1]["sources"]
    assert final_sources[0]["number"] == 1 and final_sources[0]["filename"] == "policy.txt"
    assert searches == [("refund policy", "default")]
    tool_message = llm.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool" and "[1] (policy.txt, page 2)" in tool_message["content"]
    conversation_id = events[0][1]["conversation_id"]
    history = client.get(f"/api/conversations/{conversation_id}").json()["messages"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["content"] == "You can get a refund within 7 days [1]."
    assert history[1]["sources"][0]["chunk_id"] == "doc-1_0"


def test_follow_up_questions_send_history_to_the_agent(client, monkeypatch):
    _ready_document(client)
    _fake_search(client, monkeypatch)
    llm = ScriptedLLM([
        [("text", "Refunds take 7 days [1].")],
        [("text", "It applies to all products.")],
    ])
    _use_llm(client, llm)
    first = _events(client.post("/api/chat/stream", json={"message": "refund time?"}).text)
    conversation_id = first[0][1]["conversation_id"]
    client.post("/api/chat/stream", json={"message": "does it apply to phones?", "conversation_id": conversation_id})
    sent = llm.requests[1]["messages"]
    assert [m["role"] for m in sent[1:]] == ["user", "assistant", "user"]
    assert sent[2]["content"] == "Refunds take 7 days [1]."


def test_plain_rag_mode_rewrites_follow_ups_before_searching(client, monkeypatch):
    _ready_document(client)
    retrieved = []
    monkeypatch.setattr(
        client.app.state.rag_pipeline, "retrieve",
        lambda query, namespace="default", documents=None: (retrieved.append(query) or (
            [{"number": 1, "document_id": "doc-1", "filename": "policy.txt", "page_number": None,
              "snippet": "x", "similarity": 0.9, "chunk_id": "doc-1_0"}], "[1] (policy.txt)\nx")),
    )
    settings = client.app.state.runtime_settings
    settings.current = settings.current.model_copy(update={"agent_enabled": False})
    llm = ScriptedLLM([[("text", "First [1].")], [("text", "Second [1].")]])
    _use_llm(client, llm)
    first = _events(client.post("/api/chat/stream", json={"message": "what is the refund window?"}).text)
    client.post("/api/chat/stream", json={"message": "and for phones?", "conversation_id": first[0][1]["conversation_id"]})
    assert retrieved == ["what is the refund window?", "standalone query"]


def test_greeting_and_empty_library_work_without_an_llm_key(client):
    response = client.post("/api/chat/stream", json={"message": "hi"})
    assert "Upload a document first" in response.text and "event: done" in response.text
    response = client.post("/api/chat/stream", json={"message": "Summarize it"})
    assert "Please upload a document" in response.text


def test_missing_llm_key_is_reported_before_streaming(client):
    _ready_document(client)
    response = client.post("/api/chat/stream", json={"message": "What is the refund policy?"})
    assert response.status_code == 503
    assert "GROQ_API_KEY" in response.json()["detail"]


def test_llm_failure_does_not_leave_orphan_messages(client, monkeypatch):
    from app.services.llm import LLMError

    _ready_document(client)
    _fake_search(client, monkeypatch)

    class FailingLLM(ScriptedLLM):
        def stream_chat(self, messages, tools=None):
            raise LLMError("Groq rate limit reached.", 429)
            yield  # pragma: no cover

    _use_llm(client, FailingLLM([]))
    response = client.post("/api/chat/stream", json={"message": "refund?"})
    events = _events(response.text)
    assert events[-1][0] == "error" and events[-1][1]["status_code"] == 429
    conversation = client.get(f"/api/conversations/{events[0][1]['conversation_id']}").json()
    assert conversation["messages"] == []


def test_empty_model_output_is_explained(client, monkeypatch):
    _ready_document(client)
    _fake_search(client, monkeypatch)
    _use_llm(client, ScriptedLLM([[]]))
    response = client.post("/api/chat", json={"message": "Summarize the project"})
    assert response.status_code == 200
    assert "found relevant passages" in response.json()["answer"]


def test_first_turn_answers_are_cached_and_invalidated_by_documents(client, monkeypatch):
    _ready_document(client)
    _fake_search(client, monkeypatch)
    llm = ScriptedLLM([[("text", "Seven days [1].")], [("text", "Seven days [1].")]])
    _use_llm(client, llm)
    client.post("/api/chat/stream", json={"message": "Refund window?"})
    client.post("/api/chat/stream", json={"message": "refund   window?"})
    assert len(llm.requests) == 1
    _ready_document(client, "doc-2")
    client.post("/api/chat/stream", json={"message": "Refund window?"})
    assert len(llm.requests) == 2


def test_regenerate_replaces_the_last_answer(client, monkeypatch):
    _ready_document(client)
    _fake_search(client, monkeypatch)
    _use_llm(client, ScriptedLLM([[("text", "First.")], [("text", "Second.")]]))
    first = _events(client.post("/api/chat/stream", json={"message": "Refund window?"}).text)
    conversation_id = first[0][1]["conversation_id"]
    client.post("/api/chat/stream", json={"message": "Refund window?", "conversation_id": conversation_id, "regenerate": True})
    messages = client.get(f"/api/conversations/{conversation_id}").json()["messages"]
    assert [m["content"] for m in messages] == ["Refund window?", "Second."]


def test_agent_falls_back_to_plain_rag_when_tools_are_rejected(client, monkeypatch):
    from app.services.llm import LLMError

    _ready_document(client)
    monkeypatch.setattr(
        client.app.state.rag_pipeline, "retrieve",
        lambda query, namespace="default", documents=None: (
            [{"number": 1, "document_id": "doc-1", "filename": "policy.txt", "page_number": None,
              "snippet": "x", "similarity": 0.9, "chunk_id": "doc-1_0"}], "[1] (policy.txt)\nx"),
    )

    class NoToolsLLM(ScriptedLLM):
        def stream_chat(self, messages, tools=None):
            if tools:
                raise LLMError("Groq rejected the request: tool calling is not supported", 400)
            yield ("text", "Plain answer [1].")

    _use_llm(client, NoToolsLLM([]))
    response = client.post("/api/chat", json={"message": "refund?"})
    assert response.json()["answer"] == "Plain answer [1]."


def test_chat_rate_limit(client, monkeypatch):
    settings = client.app.state.runtime_settings
    settings.current = settings.current.model_copy(update={"chat_rate_limit_per_minute": 2})
    assert client.post("/api/chat/stream", json={"message": "hi"}).status_code == 200
    assert client.post("/api/chat/stream", json={"message": "hi"}).status_code == 200
    assert client.post("/api/chat/stream", json={"message": "hi"}).status_code == 429


# ------------------------------------------------------------------ service API
@pytest.fixture
def service_client(client):
    from app.services.service_auth import ServiceKey, hash_service_key

    client.app.state.service_keys = [
        ServiceKey("marketbid", hash_service_key("mb-secret"), "marketbid"),
        ServiceKey("distos", hash_service_key("ds-secret"), "distos-*"),
    ]
    return client


def test_service_api_requires_key_and_enforces_namespace(service_client):
    plain = TestClient(service_client.app)
    assert plain.get("/api/v1/namespaces/marketbid/documents").status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert plain.get("/api/v1/namespaces/marketbid/documents", headers=bad).status_code == 401
    mb = {"Authorization": "Bearer mb-secret"}
    ds = {"Authorization": "Bearer ds-secret"}
    assert plain.get("/api/v1/namespaces/marketbid/documents", headers=mb).status_code == 200
    assert plain.get("/api/v1/namespaces/distos-acme/documents", headers=mb).status_code == 403
    assert plain.get("/api/v1/namespaces/distos-acme/documents", headers=ds).status_code == 200
    assert plain.get("/api/v1/namespaces/Bad Name/documents", headers=ds).status_code in (403, 404, 422)


def test_service_api_text_upsert_replaces_by_external_id(service_client, monkeypatch):
    service = service_client.app.state.document_service
    database = service_client.app.state.database
    monkeypatch.setattr(service, "process_document", lambda document_id: database.update_document(document_id, status="ready", chunk_count=1, error=None))
    api = TestClient(service_client.app, headers={"Authorization": "Bearer mb-secret"})
    first = api.put("/api/v1/namespaces/marketbid/documents/text", json={"title": "Refund FAQ", "text": "7 days", "external_id": "faq-1"})
    assert first.status_code == 202 and first.json()["external_id"] == "faq-1"
    second = api.put("/api/v1/namespaces/marketbid/documents/text", json={"title": "Refund FAQ", "text": "10 days", "external_id": "faq-1"})
    assert second.status_code == 202
    docs = api.get("/api/v1/namespaces/marketbid/documents").json()
    assert [doc["document_id"] for doc in docs] == [second.json()["document_id"]]
    assert service_client.get("/api/documents").json() == []  # web UI library is separate
    assert api.delete("/api/v1/namespaces/marketbid/documents/by-external-id/faq-1").status_code == 204
    assert api.get("/api/v1/namespaces/marketbid/documents").json() == []


def test_service_chat_passes_trusted_context_to_app_tools(service_client, monkeypatch):
    from app.services.tools import HttpTool, NamespaceProfile
    import app.services.agent as agent_module

    profile = NamespaceProfile(
        assistant_name="MarketBid Assistant",
        tools=[HttpTool("get_repair_status", "Repair status", {"type": "object", "properties": {"request_id": {"type": "string"}}}, "http://marketbid.test/tools/status")],
    )
    service_client.app.state.agent.agent_config.profiles = {"marketbid": profile}
    received = {}

    def fake_call(tool, arguments, namespace, trusted_context):
        received.update(tool=tool.name, arguments=arguments, namespace=namespace, context=trusted_context)
        return '{"status": "technician assigned"}'

    monkeypatch.setattr(agent_module, "call_http_tool", fake_call)
    llm = ScriptedLLM([
        [("tool_calls", [ToolCall("c1", "get_repair_status", '{"request_id": "R-55"}')])],
        [("text", "Your repair R-55 has a technician assigned.")],
    ])
    _use_llm(service_client, llm)
    api = TestClient(service_client.app, headers={"Authorization": "Bearer mb-secret"})
    response = api.post("/api/v1/namespaces/marketbid/chat", json={
        "message": "Where is my repair R-55?", "user_ref": "customer-9", "context": {"user_id": 9, "role": "customer"},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"] == "Your repair R-55 has a technician assigned."
    assert body["steps"][0]["tool"] == "get_repair_status"
    assert received == {"tool": "get_repair_status", "arguments": {"request_id": "R-55"}, "namespace": "marketbid", "context": {"user_id": 9, "role": "customer"}}
    assert "MarketBid Assistant" in llm.requests[0]["messages"][0]["content"]
    assert any(t["function"]["name"] == "get_repair_status" for t in llm.requests[0]["tools"])
    conversation_id = body["conversation_id"]
    assert api.get(f"/api/v1/namespaces/marketbid/conversations/{conversation_id}?user_ref=customer-9").status_code == 200
    assert api.get(f"/api/v1/namespaces/marketbid/conversations/{conversation_id}?user_ref=customer-10").status_code == 404
    other_user = api.post("/api/v1/namespaces/marketbid/chat", json={"message": "hi", "conversation_id": conversation_id, "user_ref": "customer-10"})
    assert other_user.status_code == 404


def test_end_to_end_upload_index_and_agent_answer(client):
    from tests.test_services import FakeEmbeddings

    fake = FakeEmbeddings()
    client.app.state.document_service.embeddings = fake
    client.app.state.rag_pipeline.embeddings = fake
    text = (
        "Screen repair price for Samsung A52 is 6500 PKR. Warranty is 30 days.\n\n"
        "Order ORD-7781 was delivered to Gulberg. Refund requests are accepted within 7 days."
    )
    upload = client.post("/api/documents/upload", files={"files": ("shop.txt", text.encode(), "text/plain")})
    assert upload.status_code == 201
    document_id = upload.json()["documents"][0]["document_id"]
    assert client.get(f"/api/documents/{document_id}").json()["status"] == "ready"

    llm = ScriptedLLM([
        [("tool_calls", [ToolCall("c1", "search_knowledge_base", '{"query": "ORD-7781 delivery"}')])],
        [("text", "ORD-7781 was delivered to Gulberg [1].")],
    ])
    _use_llm(client, llm)
    body = client.post("/api/chat", json={"message": "Where did ORD-7781 go?"}).json()
    assert body["answer"] == "ORD-7781 was delivered to Gulberg [1]."
    assert body["sources"][0]["document_id"] == document_id
    assert "ORD-7781" in llm.requests[1]["messages"][-1]["content"]

    assert client.delete(f"/api/documents/{document_id}").status_code == 204
    assert client.app.state.vector_store.count_document_chunks(document_id) == 0


def test_each_browser_sees_only_its_own_history_and_only_admins_manage_documents(client):
    admin_chat = _events(client.post("/api/chat/stream", json={"message": "hi"}).text)[0][1]["conversation_id"]

    with TestClient(client.app, headers={"X-Inkwell-Request": "1"}) as browser_a, \
         TestClient(client.app, headers={"X-Inkwell-Request": "1"}) as browser_b:
        assert browser_a.get("/api/auth/status").json()["user"]["role"] == "employee"
        assert browser_b.get("/api/auth/status").status_code == 200
        chat_a = _events(browser_a.post("/api/chat/stream", json={"message": "hello"}).text)[0][1]["conversation_id"]

        # Nobody else can list, open, rename, delete, continue or regenerate it.
        assert browser_b.get("/api/conversations").json() == []
        assert browser_b.get(f"/api/conversations/{chat_a}").status_code == 404
        assert browser_b.patch(f"/api/conversations/{chat_a}", json={"title": "x"}).status_code == 404
        assert browser_b.delete(f"/api/conversations/{chat_a}").status_code == 404
        assert browser_b.post("/api/chat/stream", json={"message": "hi", "conversation_id": chat_a}).status_code == 404
        assert browser_b.post("/api/chat/stream", json={"message": "hello", "conversation_id": chat_a, "regenerate": True}).status_code == 404
        assert browser_a.get(f"/api/conversations/{admin_chat}").status_code == 404
        assert [c["conversation_id"] for c in browser_a.get("/api/conversations").json()] == [chat_a]

        # Employees can read the shared library but never change it.
        assert browser_a.get("/api/documents").status_code == 200
        upload = browser_a.post("/api/documents/upload", files={"files": ("x.txt", b"secret", "text/plain")})
        assert upload.status_code == 403
        assert browser_a.delete("/api/documents/any-id").status_code == 403
        assert browser_a.post("/api/documents/any-id/reprocess").status_code == 403
        assert browser_a.post("/api/documents/reindex").status_code == 403
        assert browser_a.patch("/api/settings", json={"top_k": 9}).status_code == 403
        assert browser_a.get("/api/auth/users").status_code == 403

    # Without a session there is no access at all, and service routes need a key.
    anonymous = TestClient(client.app)
    assert anonymous.get(f"/api/conversations/{chat_a}").status_code == 401
    assert anonymous.get("/api/v1/namespaces/default/documents").status_code == 401
