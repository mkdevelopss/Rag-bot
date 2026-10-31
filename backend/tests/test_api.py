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


def test_stream_answer_cache_is_session_scoped_and_invalidated_by_documents(client):
    from app.services.database import utc_now

    source = {
        "document_id": "cached-document",
        "filename": "guide.pdf",
        "page_number": 2,
        "snippet": "The office opens at nine.",
        "similarity": 0.9,
        "chunk_id": "cached-document_0",
    }
    client.app.state.rag_pipeline.retrieve = lambda _question: ([source], "The office opens at nine.")

    class FakeLLM:
        def __init__(self):
            self.calls = 0

        def is_configured(self):
            return True

        def stream(self, _question, _context, _history):
            self.calls += 1
            yield "The office opens at nine."

    llm = FakeLLM()
    client.app.state.llm_service = llm

    def ask(browser):
        conversation = browser.post("/api/conversations", json={}).json()
        return browser.post(
            "/api/chat/stream",
            json={
                "message": "When does the office open?",
                "conversation_id": conversation["conversation_id"],
            },
        )

    assert ask(client).status_code == 200
    assert ask(client).status_code == 200
    assert llm.calls == 1

    client.app.state.database.create_document({
        "document_id": "cache-invalidation-document",
        "filename": "new-guide.pdf",
        "file_type": "PDF",
        "size_bytes": 100,
        "content_hash": "cache-invalidation-hash",
        "storage_path": "unused",
        "status": "ready",
        "chunk_count": 1,
        "created_at": utc_now(),
        "error": None,
    })
    assert ask(client).status_code == 200
    assert llm.calls == 2

    with TestClient(client.app, headers={"X-Inkwell-Request": "1"}) as other_browser:
        assert other_browser.get("/api/auth/status").status_code == 200
        assert ask(other_browser).status_code == 200
    assert llm.calls == 3


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


def test_streaming_chat_returns_grounded_fallback_without_matches(client, monkeypatch):
    created = client.post("/api/conversations", json={}).json()
    app = client.app
    monkeypatch.setattr(
        app.state.database,
        "list_documents",
        lambda: [{"status": "ready"}],
    )
    monkeypatch.setattr(app.state.rag_pipeline, "retrieve", lambda _query: ([], ""))
    response = client.post(
        "/api/chat/stream",
        json={"message": "What does this say?", "conversation_id": created["conversation_id"]},
    )
    assert response.status_code == 200
    assert "event: delta" in response.text
    assert "uploaded documents" in response.text
    history = client.get(f"/api/conversations/{created['conversation_id']}").json()["messages"]
    assert [message["role"] for message in history] == ["user", "assistant"]
    regenerated = client.post(
        "/api/chat/stream",
        json={
            "message": "What does this say?",
            "conversation_id": created["conversation_id"],
            "regenerate": True,
        },
    )
    assert regenerated.status_code == 200
    history = client.get(f"/api/conversations/{created['conversation_id']}").json()["messages"]
    assert [message["role"] for message in history] == ["user", "assistant"]


def test_streaming_chat_greeting_and_empty_library_prompt(client, monkeypatch):
    def unexpected_retrieval(_query):
        raise AssertionError("A greeting should not trigger document retrieval.")

    monkeypatch.setattr(client.app.state.rag_pipeline, "retrieve", unexpected_retrieval)
    created = client.post("/api/conversations", json={}).json()
    response = client.post(
        "/api/chat/stream",
        json={"message": "hi", "conversation_id": created["conversation_id"]},
    )

    assert response.status_code == 200
    assert "Hi!" in response.text
    assert "Upload a document first" in response.text
    assert "event: done" in response.text

    monkeypatch.setattr(client.app.state.rag_pipeline, "retrieve", lambda _query: ([], ""))
    no_documents = client.post(
        "/api/chat/stream",
        json={
            "message": "Summarize it",
            "conversation_id": created["conversation_id"],
        },
    )
    assert "Please upload a document" in no_documents.text


def test_streaming_chat_returns_helpful_fallback_for_unmatched_document_question(
    client, monkeypatch
):
    monkeypatch.setattr(
        client.app.state.database,
        "list_documents",
        lambda: [{"status": "ready"}],
    )
    monkeypatch.setattr(client.app.state.rag_pipeline, "retrieve", lambda _query: ([], ""))
    created = client.post("/api/conversations", json={}).json()

    response = client.post(
        "/api/chat/stream",
        json={
            "message": "What is the weather?",
            "conversation_id": created["conversation_id"],
        },
    )

    assert "couldn't find that information" in response.text
    assert "uploaded documents" in response.text


def test_streaming_chat_explains_when_model_returns_no_text(client, monkeypatch):
    source = {
        "document_id": "doc-1",
        "filename": "notes.pdf",
        "page_number": 1,
        "snippet": "The project has three phases.",
        "similarity": 0.9,
        "chunk_id": "doc-1_0",
    }
    monkeypatch.setattr(
        client.app.state.rag_pipeline,
        "retrieve",
        lambda _query: ([source], "The project has three phases."),
    )
    monkeypatch.setattr(client.app.state.llm_service, "is_configured", lambda: True)
    monkeypatch.setattr(client.app.state.llm_service, "stream", lambda *_args: iter(()))
    created = client.post("/api/conversations", json={}).json()

    response = client.post(
        "/api/chat/stream",
        json={
            "message": "Summarize the project",
            "conversation_id": created["conversation_id"],
        },
    )

    assert "found relevant passages" in response.text
    assert "event: done" in response.text
    history = client.get(f"/api/conversations/{created['conversation_id']}").json()["messages"]
    assert "found relevant passages" in history[-1]["content"]


def test_chat_endpoint_explains_when_model_returns_no_text(client, monkeypatch):
    source = {
        "document_id": "doc-1",
        "filename": "notes.pdf",
        "page_number": 1,
        "snippet": "The project has three phases.",
        "similarity": 0.9,
        "chunk_id": "doc-1_0",
    }
    monkeypatch.setattr(
        client.app.state.rag_pipeline,
        "retrieve",
        lambda _query: ([source], "The project has three phases."),
    )
    monkeypatch.setattr(client.app.state.llm_service, "stream", lambda *_args: iter(()))
    created = client.post("/api/conversations", json={}).json()

    response = client.post(
        "/api/chat",
        json={
            "message": "Summarize the project",
            "conversation_id": created["conversation_id"],
        },
    )

    assert response.status_code == 200
    assert "found relevant passages" in response.json()["answer"]
