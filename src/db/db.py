import json
import sqlite3
from pathlib import Path

from src.core import settings


def get_connection() -> sqlite3.Connection:
    db_path = Path(settings.DATABASE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    """Создаёт только таблицы, необходимые поиску вакансий и источникам."""
    with get_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS vacancies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                title TEXT,
                company TEXT,
                salary TEXT,
                score INTEGER NOT NULL,
                recommendation TEXT NOT NULL,
                vacancy_json TEXT NOT NULL,
                match_json TEXT NOT NULL,
                source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notified_at TIMESTAMP
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS telegram_sources (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                username TEXT,
                category TEXT,
                confidence INTEGER,
                is_job_source INTEGER,
                python_jobs_present INTEGER,
                last_scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active INTEGER DEFAULT 0
            )
        """)
        connection.commit()


def save_vacancy(
    fingerprint: str,
    vacancy_json: str,
    match_json: str,
    score: int,
    recommendation: str,
    source: str = "",
) -> bool:
    vacancy = json.loads(vacancy_json)
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO vacancies (
                fingerprint, title, company, salary, score,
                recommendation, vacancy_json, match_json, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fingerprint,
                vacancy.get("title"),
                vacancy.get("company"),
                vacancy.get("salary"),
                score,
                recommendation,
                vacancy_json,
                match_json,
                source,
            ),
        )
        connection.commit()
        return cursor.rowcount == 1


def mark_as_notified(fingerprint: str) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE vacancies SET notified_at = CURRENT_TIMESTAMP WHERE fingerprint = ?",
            (fingerprint,),
        )
        connection.commit()


def is_notified(fingerprint: str) -> bool:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT notified_at FROM vacancies WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
    return row is not None and row["notified_at"] is not None


def save_telegram_source(
    chat_id: int,
    title: str,
    username: str | None,
    category: str,
    confidence: int,
    is_job_source: bool,
    python_jobs_present: bool,
    active: bool,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO telegram_sources (
                chat_id, title, username, category, confidence,
                is_job_source, python_jobs_present, active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title,
                username = excluded.username,
                category = excluded.category,
                confidence = excluded.confidence,
                is_job_source = excluded.is_job_source,
                python_jobs_present = excluded.python_jobs_present,
                last_scanned_at = CURRENT_TIMESTAMP
            """,
            (
                chat_id, title, username, category, confidence,
                int(is_job_source), int(python_jobs_present), int(active),
            ),
        )
        connection.commit()


def get_active_telegram_sources() -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT chat_id, title, username FROM telegram_sources WHERE active = 1"
        ).fetchall()
    return [dict(row) for row in rows]


def get_telegram_sources() -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT chat_id, title, username, category, confidence,
                   is_job_source, python_jobs_present, active, last_scanned_at
            FROM telegram_sources
            ORDER BY confidence DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def set_telegram_source_active(chat_id: int, active: bool) -> bool:
    with get_connection() as connection:
        cursor = connection.execute(
            "UPDATE telegram_sources SET active = ? WHERE chat_id = ?",
            (int(active), chat_id),
        )
        connection.commit()
        return cursor.rowcount > 0
