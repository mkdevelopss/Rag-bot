import json
import os
import tempfile
from pathlib import Path

from app.core.config import BACKEND_DIR, Settings
from app.services.database import Database


class RuntimeSettings:
    def __init__(self, base: Settings, database: Database):
        self.database = database
        overrides = database.get_setting_overrides()
        self.current = Settings.model_validate(base.model_dump() | overrides)
        self.env_path = BACKEND_DIR / ".env"

    def get(self) -> Settings:
        return self.current

    def update_provider(self, provider: str, api_key: str | None, model: str) -> Settings:
        existing_key = (
            self.current.gemini_api_key if provider == "gemini" else self.current.groq_api_key
        )
        api_key = api_key.strip() if api_key else existing_key
        if not api_key:
            raise ValueError(f"Enter an API key for {provider.title()} before selecting it.")
        key_name = "GEMINI_API_KEY" if provider == "gemini" else "GROQ_API_KEY"
        model_name = "GEMINI_MODEL" if provider == "gemini" else "GROQ_MODEL"
        updates = {
            "LLM_PROVIDER": provider,
            key_name: api_key,
            model_name: model,
        }
        fields = {
            "llm_provider": provider,
            "gemini_api_key": api_key if provider == "gemini" else self.current.gemini_api_key,
            "gemini_model": model if provider == "gemini" else self.current.gemini_model,
            "groq_api_key": api_key if provider == "groq" else self.current.groq_api_key,
            "groq_model": model if provider == "groq" else self.current.groq_model,
        }
        candidate = self.current.model_copy(update=fields)
        self.write_env_updates(updates)
        self.current = candidate
        return candidate

    def write_env_updates(self, updates: dict[str, str]) -> None:
        path = Path(self.env_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        pending = {key: value for key, value in updates.items()}
        output = []

        for line in existing_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                output.append(line)
                continue
            key = line.split("=", 1)[0].strip()
            if key not in updates:
                output.append(line)
                continue
            if key in pending:
                output.append(f"{key}={json.dumps(pending.pop(key), ensure_ascii=True)}")

        output.extend(
            f"{key}={json.dumps(value, ensure_ascii=True)}"
            for key, value in pending.items()
        )
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as file:
                temporary_path = Path(file.name)
                file.write("\n".join(output) + "\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, path)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

    def update(self, updates: dict) -> Settings:
        candidate = Settings.model_validate(self.current.model_dump() | updates)
        self.database.set_setting_overrides(updates)
        self.current = candidate
        return candidate
