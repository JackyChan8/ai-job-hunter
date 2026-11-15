"""Описание полей профиля кандидата и разбор введённых значений."""

import re
from dataclasses import dataclass

MAX_LIST_ITEMS = 100
MAX_ITEM_LENGTH = 80
MAX_TEXT_LENGTH = 1000
MAX_EXPERIENCE_YEARS = 60

WORK_FORMATS = ("Удалённо", "Гибрид", "Офис")
EMPLOYMENT_TYPES = (
    "Полная занятость",
    "Частичная занятость",
    "Проектная работа",
    "Стажировка",
)


class ProfileInputError(ValueError):
    """Некорректный ввод; текст безопасно показывать пользователю."""


@dataclass(frozen=True)
class ProfileField:
    key: str
    label: str
    emoji: str
    kind: str  # list | int | salary | text
    hint: str
    options: tuple[str, ...] = ()
    # Значение обязательно: пустое поле не считается заполненным.
    required: bool = False

    @property
    def skippable(self) -> bool:
        """Можно ответить «не важно». Для опыта нужно явное число (0 — это тоже ответ)."""
        return not self.required and self.kind != "int"


PROFILE_FIELDS: tuple[ProfileField, ...] = (
    ProfileField(
        "target_roles", "Желаемые роли", "🎯", "list",
        "Например: Python Developer, Backend Developer, AI-инженер",
        required=True,
    ),
    ProfileField(
        "experience_years", "Опыт (лет)", "📈", "int",
        "Одно число, например: 5",
    ),
    ProfileField(
        "skills", "Навыки", "🛠", "list",
        "Технологии и инструменты. Например: Python, FastAPI, PostgreSQL, Docker",
        required=True,
    ),
    ProfileField(
        "preferred_work_formats", "Формат работы", "🏠", "list",
        "Выбери подходящие варианты.",
        options=WORK_FORMATS,
        required=True,
    ),
    ProfileField(
        "preferred_employment_types", "Тип занятости", "🕒", "list",
        "Выбери подходящие варианты.",
        options=EMPLOYMENT_TYPES,
        required=True,
    ),
    ProfileField(
        "minimum_salary", "Минимальная зарплата", "💰", "salary",
        "Число в той валюте, в которой ищешь работу. "
        "Например: 150000 или 150к",
    ),
    ProfileField(
        "languages", "Языки", "🌐", "list",
        "Например: Русский — родной, English — B2",
    ),
    ProfileField(
        "preferred_locations", "Локации", "📍", "list",
        "Города или страны. Например: Москва, Беларусь",
    ),
    ProfileField(
        "additional_information", "Дополнительно", "📝", "text",
        f"Любые пожелания и важные детали, до {MAX_TEXT_LENGTH} символов.",
    ),
)

FIELDS_BY_KEY = {field.key: field for field in PROFILE_FIELDS}

# Значение поля после нажатия «Очистить».
EMPTY_VALUES = {
    "list": [],
    "int": 0,
    "salary": None,
    "text": "",
}


def parse_list(text: str) -> list[str]:
    """Элементы через запятую, точку с запятой или с новой строки."""
    items: list[str] = []
    seen: set[str] = set()

    for raw in re.split(r"[,;\n]", text):
        item = re.sub(r"^\s*(?:[-•*–—]|\d+[.)])\s+", "", raw).strip()
        # Пустые элементы и одиночные символы вроде «-» или «•» — не значения.
        if not any(char.isalnum() for char in item):
            continue
        if len(item) > MAX_ITEM_LENGTH:
            raise ProfileInputError(
                f"Элемент слишком длинный (больше {MAX_ITEM_LENGTH} символов): "
                f"«{item[:30]}…». Раздели список запятыми."
            )
        if item.casefold() not in seen:
            seen.add(item.casefold())
            items.append(item)

    if not items:
        raise ProfileInputError("Не нашла ни одного значения. Пришли список текстом.")
    if len(items) > MAX_LIST_ITEMS:
        raise ProfileInputError(f"Слишком много элементов (максимум {MAX_LIST_ITEMS}).")

    return items


def merge_lists(current: list[str], new: list[str]) -> list[str]:
    """Добавляет новые элементы без дублей (регистр не учитывается)."""
    merged = list(current)
    seen = {item.casefold() for item in merged}

    for item in new:
        if item.casefold() not in seen:
            seen.add(item.casefold())
            merged.append(item)

    if len(merged) > MAX_LIST_ITEMS:
        raise ProfileInputError(
            f"В списке получится больше {MAX_LIST_ITEMS} элементов. "
            "Сначала удали лишнее."
        )
    return merged


def parse_experience(text: str) -> int:
    match = re.search(r"\d+", text)
    if not match:
        raise ProfileInputError("Пришли число лет, например: 5")

    years = int(match.group())
    if years > MAX_EXPERIENCE_YEARS:
        raise ProfileInputError(f"Опыт не может быть больше {MAX_EXPERIENCE_YEARS} лет.")
    return years


def parse_salary(text: str) -> int:
    cleaned = re.sub(r"[\s_,]", "", text.lower())
    match = re.fullmatch(r"(\d+)(k|к|тыс\.?|тысяч)?", cleaned)

    if not match:
        raise ProfileInputError(
            "Пришли только число, например: 150000 или 150к (без валюты)."
        )

    amount = int(match.group(1))
    if match.group(2):
        amount *= 1000
    return amount


def parse_text(text: str) -> str:
    value = text.strip()
    if len(value) > MAX_TEXT_LENGTH:
        raise ProfileInputError(
            f"Слишком длинный текст ({len(value)} символов, максимум {MAX_TEXT_LENGTH})."
        )
    return value


def parse_field_value(field: ProfileField, text: str):
    if field.kind == "list":
        return parse_list(text)
    if field.kind == "int":
        return parse_experience(text)
    if field.kind == "salary":
        return parse_salary(text)
    return parse_text(text)


def format_value(field: ProfileField, value, max_items: int = 15) -> str:
    if field.kind == "list":
        if not value:
            return "—"
        shown = ", ".join(value[:max_items])
        extra = len(value) - max_items
        return f"{shown} … (+{extra})" if extra > 0 else shown

    if field.kind == "salary":
        return "не указана" if value is None else f"{value:,}".replace(",", " ")

    if field.kind == "int":
        return str(value)

    if not value:
        return "—"
    return value if len(value) <= 300 else value[:300] + "…"
