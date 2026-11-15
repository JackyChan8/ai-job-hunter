from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenRouter
    OPENROUTER_API_KEY: SecretStr
    OPENROUTER_MODEL: str = "nvidia/nemotron-3.5-lightning:free"
    OPENROUTER_VISION_MODEL: str = "google/gemini-2.5-flash"  # Модель с поддержкой изображений

    # LangSmith tracing is optional
    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: SecretStr | None = None
    LANGCHAIN_PROJECT: str = "AI-Job-Hunter"

    # Telegram Bot API
    TELEGRAM_BOT_TOKEN: SecretStr
    TELEGRAM_CHAT_ID: str
    TELEGRAM_ADMIN_USER_ID: int

    # SQLite
    DATABASE_PATH: str = "data/job_hunter.db"

    # Telethon user session
    TELEGRAM_API_ID: int
    TELEGRAM_API_HASH: SecretStr
    TELEGRAM_SESSION_PATH: str = "credentials/telegram_user"

    # Candidate files
    CANDIDATE_PROFILE_PATH: str = "data/candidate_profile.json"
    RESUME_PATH: str = "data/resume.pdf"

    # Pipeline
    VACANCY_MIN_SCORE: int = 60
    COVER_LETTER_MAX_ATTEMPTS: int = 2


settings = AppSettings()  # pyright: ignore[reportCallIssue]
