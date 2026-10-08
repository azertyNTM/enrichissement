"""Configuration lue depuis l'environnement (et un éventuel fichier .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Charge un .env minimaliste (CLE=valeur) sans écraser l'environnement existant."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "oui", "on"}


@dataclass
class Settings:
    db_path: str = "enrichissement.db"
    search_provider: str = "brave"
    brave_api_key: str = ""
    serper_api_key: str = ""
    user_agent: str = "EnrichissementBot/0.1"
    http_timeout: float = 10.0
    http_min_interval: float = 1.0
    smtp_enabled: bool = False
    smtp_helo_host: str = "localhost"
    smtp_mail_from: str = "verif@example.com"
    smtp_timeout: float = 15.0
    smtp_pause_min: float = 20.0
    smtp_pause_max: float = 40.0
    smtp_daily_cap: int = 150
    export_min_score: int = 85
    retention_years: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        e = os.environ.get
        return cls(
            db_path=e("DB_PATH", cls.db_path),
            search_provider=e("SEARCH_PROVIDER", cls.search_provider).lower(),
            brave_api_key=e("BRAVE_API_KEY", ""),
            serper_api_key=e("SERPER_API_KEY", ""),
            user_agent=e("USER_AGENT", cls.user_agent),
            http_timeout=float(e("HTTP_TIMEOUT", cls.http_timeout)),
            http_min_interval=float(e("HTTP_MIN_INTERVAL", cls.http_min_interval)),
            smtp_enabled=_bool(e("SMTP_ENABLED"), False),
            smtp_helo_host=e("SMTP_HELO_HOST", cls.smtp_helo_host),
            smtp_mail_from=e("SMTP_MAIL_FROM", cls.smtp_mail_from),
            smtp_timeout=float(e("SMTP_TIMEOUT", cls.smtp_timeout)),
            smtp_pause_min=float(e("SMTP_PAUSE_MIN", cls.smtp_pause_min)),
            smtp_pause_max=float(e("SMTP_PAUSE_MAX", cls.smtp_pause_max)),
            smtp_daily_cap=int(e("SMTP_DAILY_CAP", cls.smtp_daily_cap)),
            export_min_score=int(e("EXPORT_MIN_SCORE", cls.export_min_score)),
            retention_years=int(e("RETENTION_YEARS", cls.retention_years)),
        )
