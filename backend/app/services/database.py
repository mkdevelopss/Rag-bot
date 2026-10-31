import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    content_hash TEXT NOT NULL UNIQUE,
                    storage_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT '',
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    sources TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'employee')),
                    is_active INTEGER NOT NULL DEFAULT 1,
                    must_change_password INTEGER NOT NULL DEFAULT 1,
                    is_guest INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    session_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS auth_sessions_user_id_idx
                    ON auth_sessions(user_id);
                CREATE TABLE IF NOT EXISTS login_attempts (
                    email TEXT PRIMARY KEY COLLATE NOCASE,
                    failed_count INTEGER NOT NULL,
                    window_started_at TEXT NOT NULL,
                    locked_until TEXT
                );
                """
            )
            conversation_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(conversations)")
            }
            if "owner_id" not in conversation_columns:
                connection.execute(
                    "ALTER TABLE conversations ADD COLUMN owner_id TEXT REFERENCES users(user_id)"
                )
            user_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(users)")
            }
            if "is_guest" not in user_columns:
                connection.execute(
                    "ALTER TABLE users ADD COLUMN is_guest INTEGER NOT NULL DEFAULT 0"
                )
            document_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(documents)")
            }
            if "updated_at" not in document_columns:
                connection.execute(
                    "ALTER TABLE documents ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                "UPDATE documents SET updated_at = created_at WHERE updated_at = ''"
            )

    @staticmethod
    def _user(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["is_active"] = bool(data["is_active"])
        data["must_change_password"] = bool(data["must_change_password"])
        data.pop("password_hash", None)
        return data

    def count_users(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()
        return int(row["count"])

    def count_admins(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM users WHERE role = 'admin' AND is_active = 1"
            ).fetchone()
        return int(row["count"])

    def create_user(
        self,
        email: str,
        display_name: str,
        password_hash: str,
        role: str,
        must_change_password: bool,
    ) -> dict[str, Any]:
        user_id = str(uuid4())
        now = utc_now()
        with self.connect() as connection:
            existing_count = connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
            connection.execute(
                """INSERT INTO users
                (user_id, email, display_name, password_hash, role, is_active,
                 must_change_password, is_guest, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, 0, ?)""",
                (user_id, email, display_name, password_hash, role, int(must_change_password), now),
            )
            if existing_count == 0 and role == "admin":
                connection.execute(
                    "UPDATE conversations SET owner_id = ? WHERE owner_id IS NULL",
                    (user_id,),
                )
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        user = self._user(row)
        if user is None:
            raise RuntimeError("The newly created user could not be loaded.")
        return user

    def create_guest_user(self) -> dict[str, Any]:
        user_id = str(uuid4())
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO users
                (user_id, email, display_name, password_hash, role, is_active,
                 must_change_password, is_guest, created_at)
                VALUES (?, ?, 'Employee', ?, 'employee', 1, 0, 1, ?)""",
                (user_id, f"guest+{user_id}@session.invalid", uuid4().hex, now),
            )
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        user = self._user(row)
        if user is None:
            raise RuntimeError("The employee session could not be created.")
        return user

    def promote_guest_to_admin(self, user_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE users SET role = 'admin', display_name = 'Company Admin',
                is_guest = 0, email = ? WHERE user_id = ? AND role = 'employee'
                AND is_guest = 1 AND NOT EXISTS (
                    SELECT 1 FROM users WHERE role = 'admin' AND is_active = 1
                )""",
                (f"admin+{user_id}@session.invalid", user_id),
            )
            if cursor.rowcount != 1:
                return None
            connection.execute(
                "UPDATE conversations SET owner_id = ? WHERE owner_id IS NULL",
                (user_id,),
            )
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return self._user(row)

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)
            ).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return self._user(row)

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT user_id, email, display_name, role, is_active,
                must_change_password, created_at, last_login_at
                FROM users WHERE is_guest = 0 ORDER BY created_at"""
            ).fetchall()
        return [user for row in rows if (user := self._user(row)) is not None]

    def create_session(self, session_hash: str, user_id: str, expires_at: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO auth_sessions VALUES (?, ?, ?, ?)",
                (session_hash, user_id, expires_at, utc_now()),
            )
            connection.execute(
                "UPDATE users SET last_login_at = ? WHERE user_id = ?",
                (utc_now(), user_id),
            )

    def get_user_by_session(self, session_hash: str) -> dict[str, Any] | None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
            row = connection.execute(
                """SELECT users.* FROM auth_sessions
                JOIN users ON users.user_id = auth_sessions.user_id
                WHERE auth_sessions.session_hash = ?
                AND auth_sessions.expires_at > ? AND users.is_active = 1""",
                (session_hash, now),
            ).fetchone()
        return self._user(row)

    def delete_session(self, session_hash: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE session_hash = ?", (session_hash,))

    def update_user_password(
        self,
        user_id: str,
        password_hash: str,
        current_session_hash: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE users SET password_hash = ?, must_change_password = 0
                WHERE user_id = ?""",
                (password_hash, user_id),
            )
            connection.execute(
                "DELETE FROM auth_sessions WHERE user_id = ? AND session_hash != ?",
                (user_id, current_session_hash),
            )

    def set_user_active(self, user_id: str, is_active: bool) -> bool:
        with self.connect() as connection:
            user = connection.execute(
                "SELECT role, is_active FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            if user is None:
                return False
            if user["role"] == "admin" and user["is_active"] and not is_active:
                active_admins = connection.execute(
                    "SELECT COUNT(*) AS count FROM users WHERE role = 'admin' AND is_active = 1"
                ).fetchone()["count"]
                if active_admins <= 1:
                    raise ValueError("The last active administrator cannot be deactivated.")
            connection.execute(
                "UPDATE users SET is_active = ? WHERE user_id = ?",
                (int(is_active), user_id),
            )
            if not is_active:
                connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        return True

    def reset_user_password(self, user_id: str, password_hash: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE users SET password_hash = ?, must_change_password = 1
                WHERE user_id = ? AND role = 'employee'""",
                (password_hash, user_id),
            )
            if cursor.rowcount:
                connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        return cursor.rowcount > 0

    def is_login_locked(self, email: str) -> bool:
        now = utc_now()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT locked_until FROM login_attempts WHERE email = ?",
                (email,),
            ).fetchone()
        return bool(row and row["locked_until"] and row["locked_until"] > now)

    def record_login_failure(self, email: str) -> None:
        now = datetime.now(timezone.utc)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT failed_count, window_started_at FROM login_attempts WHERE email = ?",
                (email,),
            ).fetchone()
            if row is None or (now - datetime.fromisoformat(row["window_started_at"])).total_seconds() > 900:
                count = 1
                window_started_at = now.isoformat()
            else:
                count = row["failed_count"] + 1
                window_started_at = row["window_started_at"]
            locked_until = (now + timedelta(minutes=1)).isoformat() if count >= 5 else None
            connection.execute(
                """INSERT INTO login_attempts(email, failed_count, window_started_at, locked_until)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET failed_count = excluded.failed_count,
                window_started_at = excluded.window_started_at, locked_until = excluded.locked_until""",
                (email, count, window_started_at, locked_until),
            )

    def clear_login_failures(self, email: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM login_attempts WHERE email = ?", (email,))

    @staticmethod
    def _document(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data.pop("content_hash", None)
        data.pop("storage_path", None)
        return data

    def find_document_by_hash(self, content_hash: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE content_hash = ?", (content_hash,)
            ).fetchone()
        return self._document(row)

    def create_document(self, document: dict[str, Any]) -> None:
        values = {**document, "updated_at": document.get("updated_at") or document["created_at"]}
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO documents
                (document_id, filename, file_type, size_bytes, content_hash, storage_path,
                 status, chunk_count, created_at, updated_at, error)
                VALUES (:document_id, :filename, :file_type, :size_bytes, :content_hash,
                        :storage_path, :status, :chunk_count, :created_at, :updated_at, :error)""",
                values,
            )

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        return self._document(row)

    def get_document_storage_path(self, document_id: str) -> Path | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT storage_path FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        return Path(row["storage_path"]) if row else None

    def list_documents(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM documents ORDER BY created_at DESC"
            ).fetchall()
        return [self._document(row) for row in rows]  # type: ignore[misc]

    def update_document(self, document_id: str, **fields: Any) -> None:
        allowed = {"status", "chunk_count", "error"}
        if not fields or set(fields) - allowed:
            raise ValueError("Invalid document fields")
        assignments = ", ".join([*(f"{key} = ?" for key in fields), "updated_at = ?"])
        with self.connect() as connection:
            connection.execute(
                f"UPDATE documents SET {assignments} WHERE document_id = ?",
                (*fields.values(), utc_now(), document_id),
            )

    def delete_document(self, document_id: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))

    def create_conversation(self, user_id: str, title: str = "New chat") -> dict[str, Any]:
        conversation_id = str(uuid4())
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO conversations
                (conversation_id, title, created_at, updated_at, owner_id)
                VALUES (?, ?, ?, ?, ?)""",
                (conversation_id, title, now, now, user_id),
            )
        return {
            "conversation_id": conversation_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }

    def list_conversations(self, user_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM conversations WHERE owner_id = ? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_conversation(self, conversation_id: str, user_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = ? AND owner_id = ?",
                (conversation_id, user_id),
            ).fetchone()
            if conversation is None:
                return None
            messages = connection.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at",
                (conversation_id,),
            ).fetchall()
        result = dict(conversation)
        result["messages"] = [
            {
                **dict(message),
                "sources": json.loads(message["sources"]),
            }
            for message in messages
        ]
        return result

    def rename_conversation(self, conversation_id: str, user_id: str, title: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE conversations SET title = ?, updated_at = ?
                WHERE conversation_id = ? AND owner_id = ?""",
                (title, utc_now(), conversation_id, user_id),
            )
        return cursor.rowcount > 0

    def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM conversations WHERE conversation_id = ? AND owner_id = ?",
                (conversation_id, user_id),
            )
        return cursor.rowcount > 0

    def add_message(
        self,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
        sources: list[dict[str, Any]] | None = None,
    ) -> None:
        now = utc_now()
        with self.connect() as connection:
            existing = connection.execute(
                """SELECT title FROM conversations
                WHERE conversation_id = ? AND owner_id = ?""",
                (conversation_id, user_id),
            ).fetchone()
            if existing is None:
                raise KeyError("Conversation does not exist")
            connection.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid4()), conversation_id, role, content, json.dumps(sources or []), now),
            )
            if role == "user" and existing["title"] == "New chat":
                title = content.strip().replace("\n", " ")[:48] or "New chat"
                connection.execute(
                    "UPDATE conversations SET title = ?, updated_at = ? WHERE conversation_id = ?",
                    (title, now, conversation_id),
                )
            else:
                connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                    (now, conversation_id),
                )

    def prepare_regeneration(
        self, conversation_id: str, user_id: str, expected_question: str
    ) -> list[dict[str, Any]] | None:
        with self.connect() as connection:
            owned = connection.execute(
                "SELECT 1 FROM conversations WHERE conversation_id = ? AND owner_id = ?",
                (conversation_id, user_id),
            ).fetchone()
            if owned is None:
                return None
            latest = connection.execute(
                """SELECT message_id, role, content FROM messages
                WHERE conversation_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 2""",
                (conversation_id,),
            ).fetchall()
            if (
                len(latest) != 2
                or latest[0]["role"] != "assistant"
                or latest[1]["role"] != "user"
                or latest[1]["content"] != expected_question
            ):
                return None
            rows = connection.execute(
                """SELECT role, content FROM messages WHERE conversation_id = ?
                ORDER BY created_at, rowid""",
                (conversation_id,),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows[:-2]]

    def replace_last_assistant(
        self,
        conversation_id: str,
        user_id: str,
        expected_question: str,
        content: str,
        sources: list[dict[str, Any]] | None = None,
    ) -> None:
        with self.connect() as connection:
            owned = connection.execute(
                "SELECT 1 FROM conversations WHERE conversation_id = ? AND owner_id = ?",
                (conversation_id, user_id),
            ).fetchone()
            if owned is None:
                raise KeyError("Conversation does not exist")
            latest = connection.execute(
                """SELECT message_id, role, content FROM messages
                WHERE conversation_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 2""",
                (conversation_id,),
            ).fetchall()
            if (
                len(latest) != 2
                or latest[0]["role"] != "assistant"
                or latest[1]["role"] != "user"
                or latest[1]["content"] != expected_question
            ):
                raise KeyError("The answer to regenerate no longer exists")
            connection.execute("DELETE FROM messages WHERE message_id = ?", (latest[0]["message_id"],))
            connection.execute(
                "INSERT INTO messages VALUES (?, ?, 'assistant', ?, ?, ?)",
                (
                    str(uuid4()),
                    conversation_id,
                    content,
                    json.dumps(sources or []),
                    utc_now(),
                ),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (utc_now(), conversation_id),
            )

    def get_setting_overrides(self) -> dict[str, Any]:
        with self.connect() as connection:
            rows = connection.execute("SELECT setting_key, setting_value FROM app_settings").fetchall()
        return {row["setting_key"]: json.loads(row["setting_value"]) for row in rows}

    def set_setting_overrides(self, updates: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO app_settings(setting_key, setting_value) VALUES (?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value""",
                [(key, json.dumps(value)) for key, value in updates.items()],
            )
