from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        self.host = os.getenv("RESILIO_HOST", "127.0.0.1")
        self.port = int(os.getenv("RESILIO_PORT", "8000"))
        self.database_path = Path(os.getenv("RESILIO_DB_PATH", "data/resillo.sqlite3"))
        self.ollama_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "gemma:2b")
        self.connect_timeout = float(os.getenv("OLLAMA_CONNECT_TIMEOUT", "0.5"))
        self.read_timeout = float(os.getenv("OLLAMA_READ_TIMEOUT", "2.0"))
        self.max_payload = int(os.getenv("RESILIO_MAX_PAYLOAD", "65536"))
        self.log_dir = Path(os.getenv("RESILIO_LOG_DIR", "data/logs"))
        self.log_max_bytes = int(os.getenv("RESILIO_LOG_MAX_BYTES", "1048576"))
        self.log_backup_count = int(os.getenv("RESILIO_LOG_BACKUP_COUNT", "5"))
        self.api_token = os.getenv("RESILIO_API_TOKEN") or self._load_token()
        self.cors_origins = tuple(
            item.strip() for item in os.getenv(
                "CORS_ORIGINS",
                "http://127.0.0.1:8000,http://localhost:8000,capacitor://localhost,http://localhost",
            ).split(",") if item.strip()
        )

    def _load_token(self) -> str:
        token_path = self.database_path.with_suffix(".token")
        token_path.parent.mkdir(parents=True, exist_ok=True)
        if token_path.exists():
            return token_path.read_text(encoding="utf-8").strip()
        token = secrets.token_urlsafe(32)
        token_path.write_text(token, encoding="utf-8")
        return token

    def token_digest(self) -> str:
        return hashlib.sha256(self.api_token.encode("utf-8")).hexdigest()


settings = Settings()
