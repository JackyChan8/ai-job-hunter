"""Профиль и резюме кандидата.

Приоритет источников:
1. Данные, загруженные через бота (SQLite, общий том для бота и Listener).
2. Файлы CANDIDATE_PROFILE_PATH и RESUME_PATH — запасной вариант.

Данные читаются при каждом обращении, поэтому изменения из бота
сразу применяются в Listener без перезапуска.
"""

import io
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field
from pypdf import PdfReader

from src.core import settings
from src.db import get_candidate_value, set_candidate_value
from src.services.profile_fields import FIELDS_BY_KEY, PROFILE_FIELDS

logger = logging.getLogger(__name__)

PROFILE_KEY = "profile"
RESUME_TEXT_KEY = "resume_text"
RESUME_META_KEY = "resume_meta"
CONFIRMED_KEY = "profile_confirmed"

MIN_RESUME_CHARS = 200
MAX_RESUME_CHARS = 20_000


class CandidateProfile(BaseModel):
    target_roles: list[str] = Field(default_factory=list)
    experience_years: int = Field(default=0, ge=0, le=60)
    skills: list[str] = Field(default_factory=list)
    preferred_work_formats: list[str] = Field(default_factory=list)
    preferred_employment_types: list[str] = Field(default_factory=list)
    minimum_salary: int | None = Field(default=None, ge=0)
    preferred_locations: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    additional_information: str = ""


class ResumeError(Exception):
    """Ошибка, текст которой безопасно показывать пользователю."""


# ---------------------------------------------------------------------------
# Профиль
# ---------------------------------------------------------------------------


def _load_profile_file() -> CandidateProfile:
    path = Path(settings.CANDIDATE_PROFILE_PATH)
    if not path.exists():
        logger.warning("Профиль кандидата не найден (%s). Профиль пуст.", path)
        return CandidateProfile()

    with path.open("r", encoding="utf-8") as file:
        return CandidateProfile.model_validate(json.load(file))


def load_candidate_profile() -> dict:
    stored = get_candidate_value(PROFILE_KEY)

    if stored:
        try:
            return CandidateProfile.model_validate_json(stored).model_dump()
        except ValueError:
            logger.exception("Сохранённый профиль повреждён, используем файл")

    return _load_profile_file().model_dump()


def update_candidate_profile(field: str, value) -> dict:
    """Меняет одно поле профиля. При неверном значении бросает ValueError."""
    if field not in CandidateProfile.model_fields:
        raise ValueError(f"Неизвестное поле профиля: {field}")

    profile = CandidateProfile.model_validate(
        {**load_candidate_profile(), field: value}
    )
    set_candidate_value(PROFILE_KEY, profile.model_dump_json())
    _confirm(field)  # сохранение значения — это явный ответ пользователя
    return profile.model_dump()


def get_confirmed_fields() -> set[str]:
    """Поля, на которые пользователь ответил в боте."""
    raw = get_candidate_value(CONFIRMED_KEY)
    if not raw:
        return set()

    try:
        data = json.loads(raw)
    except ValueError:
        return set()

    if not isinstance(data, list):
        return set()
    return {key for key in data if key in FIELDS_BY_KEY}


def _confirm(*keys: str) -> None:
    confirmed = get_confirmed_fields() | set(keys)
    set_candidate_value(CONFIRMED_KEY, json.dumps(sorted(confirmed)))


def confirm_field(field: str) -> None:
    """Подтверждает текущее значение поля («оставить как есть»).

    Значение фиксируется в БД, чтобы дальнейшие правки файла профиля
    не меняли его незаметно."""
    if field not in FIELDS_BY_KEY:
        raise ValueError(f"Неизвестное поле профиля: {field}")

    profile = CandidateProfile.model_validate(load_candidate_profile())
    set_candidate_value(PROFILE_KEY, profile.model_dump_json())
    _confirm(field)


# ---------------------------------------------------------------------------
# Резюме
# ---------------------------------------------------------------------------


def extract_resume_text_from_pdf(data: bytes) -> tuple[str, int]:
    """Возвращает (текст, число страниц). Синхронная: вызывать через to_thread."""
    try:
        reader = PdfReader(io.BytesIO(data))

        if reader.is_encrypted and not reader.decrypt(""):
            raise ResumeError("PDF защищён паролем. Сними защиту и пришли файл снова.")

        pages = len(reader.pages)
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()

    except ResumeError:
        raise
    except Exception as error:
        raise ResumeError(
            "Не удалось прочитать файл. Проверь, что это обычный PDF."
        ) from error

    if len(text) < MIN_RESUME_CHARS:
        raise ResumeError(
            "В PDF почти нет текста — похоже, это скан или картинка. "
            "Пришли PDF с текстовым слоем (например, выгруженный из hh.ru, "
            "Google Docs или Word)."
        )

    return text[:MAX_RESUME_CHARS], pages


def save_resume(text: str, filename: str, pages: int) -> None:
    set_candidate_value(RESUME_TEXT_KEY, text)
    set_candidate_value(
        RESUME_META_KEY,
        json.dumps(
            {
                "filename": filename,
                "pages": pages,
                "chars": len(text),
                "uploaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
        ),
    )


def _read_resume_file() -> str:
    path = Path(settings.RESUME_PATH)
    if not path.exists():
        logger.warning("Резюме не найдено (%s). Будет использован только профиль.", path)
        return ""

    try:
        text, _ = extract_resume_text_from_pdf(path.read_bytes())
        return text
    except ResumeError:
        logger.exception("Не удалось прочитать PDF-резюме из файла")
        return ""


def load_resume_text() -> str:
    return get_candidate_value(RESUME_TEXT_KEY) or _read_resume_file()


def get_resume_status() -> dict:
    """source: uploaded (из бота), file (из RESUME_PATH) или none."""
    raw_meta = get_candidate_value(RESUME_META_KEY)

    if raw_meta and get_candidate_value(RESUME_TEXT_KEY):
        try:
            return {"source": "uploaded", **json.loads(raw_meta)}
        except ValueError:
            logger.exception("Метаданные резюме повреждены")

    path = Path(settings.RESUME_PATH)
    if path.exists():
        return {"source": "file", "filename": path.name}

    return {"source": "none"}


# ---------------------------------------------------------------------------
# Готовность к работе
# ---------------------------------------------------------------------------

RESUME_STEP = "resume"


@dataclass(frozen=True)
class SetupStatus:
    """Мониторинг и письма запускаются, только когда настройка завершена."""

    resume_ok: bool
    incomplete_fields: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return self.resume_ok and not self.incomplete_fields

    @property
    def next_step(self) -> str | None:
        """RESUME_STEP, ключ поля профиля или None, если всё готово."""
        if not self.resume_ok:
            return RESUME_STEP
        return self.incomplete_fields[0] if self.incomplete_fields else None

    @property
    def missing_labels(self) -> list[str]:
        labels = [] if self.resume_ok else ["Резюме (PDF)"]
        labels.extend(FIELDS_BY_KEY[key].label for key in self.incomplete_fields)
        return labels


def get_setup_status() -> SetupStatus:
    """Резюме загружено через бота, а каждое поле профиля подтверждено.

    Обязательные поля (роли, навыки, форматы и типы занятости) не могут быть
    пустыми. Остальные можно явно оставить пустыми («не важно»).
    Данные из файлов-заготовок сами по себе не считаются заполненными."""
    profile = load_candidate_profile()
    confirmed = get_confirmed_fields()

    incomplete = tuple(
        field.key
        for field in PROFILE_FIELDS
        if field.key not in confirmed or (field.required and not profile.get(field.key))
    )

    return SetupStatus(
        resume_ok=get_resume_status()["source"] == "uploaded",
        incomplete_fields=incomplete,
    )
