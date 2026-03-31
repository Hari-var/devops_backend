from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()  # load .env immediately so os.getenv calls in AppSettings see the values


def load_env() -> None:
    """No-op kept for backwards compatibility — .env is loaded at module import."""
    pass


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise EnvironmentError(f"Required environment variable '{name}' is not set.")
    return value


def get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


@dataclass(frozen=True)
class AppSettings:
    """Immutable application settings — all values from environment variables."""

    # Server
    frontend_url: str = field(default_factory=lambda: os.getenv("FRONTEND_URL", "http://localhost:5173"))
    cookie_secure: bool = field(default_factory=lambda: os.getenv("COOKIE_SECURE", "").lower() == "true")
    cors_origins: list[str] = field(
        default_factory=lambda: [
            o.strip()
            for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
            if o.strip()
        ]
    )

    # GitHub OAuth
    github_client_id: str = field(default_factory=lambda: os.getenv("GITHUB_CLIENT_ID", ""))
    github_client_secret: str = field(default_factory=lambda: os.getenv("GITHUB_CLIENT_SECRET", ""))
    github_oauth_callback_url: str = field(default_factory=lambda: os.getenv("GITHUB_OAUTH_CALLBACK_URL", ""))
    github_pat: str = field(default_factory=lambda: os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", ""))

    # Azure
    azure_client_id: str = field(default_factory=lambda: os.getenv("AZURE_CLIENT_ID", ""))
    azure_client_secret: str = field(default_factory=lambda: os.getenv("AZURE_CLIENT_SECRET", ""))
    azure_tenant_id: str = field(default_factory=lambda: os.getenv("AZURE_TENANT_ID", ""))
    azure_subscription_id: str = field(default_factory=lambda: os.getenv("AZURE_SUBSCRIPTION_ID", ""))

    # AI
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))

    # Security tools
    sonar_host_url: str = field(default_factory=lambda: os.getenv("SONAR_HOST_URL", ""))
    sonar_token: str = field(default_factory=lambda: os.getenv("SONAR_TOKEN", ""))
    sonar_project_key_prefix: str = field(default_factory=lambda: os.getenv("SONAR_PROJECT_KEY_PREFIX", ""))
    zap_api_key: str = field(default_factory=lambda: os.getenv("ZAP_API_KEY", ""))
    zap_base_url: str = field(default_factory=lambda: os.getenv("ZAP_BASE_URL", ""))

    # GitHub Webhook
    github_webhook_secret: str = field(default_factory=lambda: os.getenv("GITHUB_WEBHOOK_SECRET", ""))

    # Poller
    approval_poll_interval: int = field(
        default_factory=lambda: int(os.getenv("APPROVAL_POLL_INTERVAL", "60"))
    )

    # Database
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./devops_agent.db")
    )


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return the cached application settings singleton."""
    return AppSettings()
