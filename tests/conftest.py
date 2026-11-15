import os
import tempfile

import pytest

# Заглушки обязательных настроек: должны быть заданы до импорта src.core.
os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")
os.environ.setdefault("TELEGRAM_ADMIN_USER_ID", "42")
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "hash")

# Тесты не должны трогать реальные БД, резюме и профиль из проекта.
_TMP = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "test.db")
os.environ["RESUME_PATH"] = os.path.join(_TMP, "no_resume.pdf")
os.environ["CANDIDATE_PROFILE_PATH"] = os.path.join(_TMP, "no_profile.json")


@pytest.fixture(autouse=True)
def clean_db():
    """Каждый тест начинает с пустой БД."""
    from src.db import get_connection, init_db

    init_db()
    with get_connection() as connection:
        connection.execute("DELETE FROM candidate_data")
        connection.execute("DELETE FROM vacancies")
        connection.commit()


def _build_pdf(text: str) -> bytes:
    """Минимальный валидный PDF с одной строкой текста (только ASCII)."""
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 10 Tf 20 700 Td ({safe}) Tj ET".encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF"
    ).encode()
    return bytes(out)


@pytest.fixture
def make_pdf():
    return _build_pdf


@pytest.fixture
def setup_done():
    """Резюме загружено, все поля профиля заполнены и подтверждены."""
    from src.services.candidate import save_resume, update_candidate_profile

    save_resume("Resume text. " * 30, "cv.pdf", 1)

    values = {
        "target_roles": ["Python Developer"],
        "experience_years": 5,
        "skills": ["Python", "FastAPI"],
        "preferred_work_formats": ["Удалённо"],
        "preferred_employment_types": ["Полная занятость"],
        "minimum_salary": 100000,
        "languages": ["Русский"],
        "preferred_locations": ["Москва"],
        "additional_information": "—",
    }
    for key, value in values.items():
        update_candidate_profile(key, value)
