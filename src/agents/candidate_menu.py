"""Меню бота: загрузка резюме и редактирование профиля кандидата.

Состояние диалога («жду вакансию», «жду PDF», «жду значение поля»)
хранится в context.user_data и сбрасывается по /cancel или через 15 минут.
"""

import asyncio
import logging
import time
from collections.abc import Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import ContextTypes

from src.services.candidate import (
    RESUME_STEP,
    ResumeError,
    SetupStatus,
    confirm_field,
    extract_resume_text_from_pdf,
    get_resume_status,
    get_setup_status,
    load_candidate_profile,
    save_resume,
    update_candidate_profile,
)
from src.services.profile_fields import (
    EMPTY_VALUES,
    FIELDS_BY_KEY,
    PROFILE_FIELDS,
    ProfileField,
    ProfileInputError,
    format_value,
    merge_lists,
    parse_field_value,
)

logger = logging.getLogger(__name__)

MODE_KEY = "mode"
MODE_VACANCY = "vacancy"
MODE_RESUME = "resume"
MODE_FIELD = "field"
MODE_TTL_SECONDS = 15 * 60

# Пока true, после каждого шага бот сам предлагает следующий.
ONBOARDING_KEY = "onboarding"

MAX_RESUME_BYTES = 10 * 1024 * 1024
RESUME_PREVIEW_CHARS = 300

CALLBACK_PREFIX = "p"

Screen = tuple[str, InlineKeyboardMarkup | None]


# ---------------------------------------------------------------------------
# Режимы диалога
# ---------------------------------------------------------------------------


def set_mode(user_data: dict, name: str, **extra) -> None:
    user_data[MODE_KEY] = {"name": name, "at": time.monotonic(), **extra}


def get_mode(user_data: dict) -> dict | None:
    mode = user_data.get(MODE_KEY)
    if mode is None:
        return None

    if time.monotonic() - mode["at"] > MODE_TTL_SECONDS:
        user_data.pop(MODE_KEY, None)
        return None

    return mode


def clear_mode(user_data: dict) -> None:
    user_data.pop(MODE_KEY, None)


def is_onboarding(user_data: dict) -> bool:
    return bool(user_data.get(ONBOARDING_KEY))


def cancel_onboarding(user_data: dict) -> None:
    user_data.pop(ONBOARDING_KEY, None)


def _step_number(step: str) -> int:
    """Резюме — шаг 1, затем поля профиля по порядку."""
    if step == RESUME_STEP:
        return 1
    return 2 + [field.key for field in PROFILE_FIELDS].index(step)


TOTAL_STEPS = 1 + len(PROFILE_FIELDS)


# ---------------------------------------------------------------------------
# Кнопки
# ---------------------------------------------------------------------------


def _button(text: str, *parts: str | int) -> InlineKeyboardButton:
    data = ":".join([CALLBACK_PREFIX, *map(str, parts)])
    return InlineKeyboardButton(text, callback_data=data)


def _back_row(text: str = "⬅️ К профилю") -> list[InlineKeyboardButton]:
    return [_button(text, "back")]


class CandidateMenu:
    def __init__(self, is_admin: Callable[[Update], bool]):
        self.is_admin = is_admin

    # ------------------------------------------------------------------
    # Экраны
    # ------------------------------------------------------------------

    @staticmethod
    def _resume_status_text() -> str:
        status = get_resume_status()

        if status["source"] == "uploaded":
            date = (status.get("uploaded_at") or "")[:10]
            return (
                f"Сейчас используется: {status['filename']} "
                f"({status.get('pages', '?')} стр., "
                f"{status.get('chars', '?')} символов, загружено {date})"
            )

        if status["source"] == "file":
            return f"Сейчас используется файл с сервера: {status['filename']}"

        return "Резюме пока не загружено, письма пишутся только по профилю."

    def _profile_screen(self) -> Screen:
        profile = load_candidate_profile()
        status = get_setup_status()

        lines = ["👤 ПРОФИЛЬ КАНДИДАТА", ""]

        if status.complete:
            lines += ["✅ Настройка завершена.", ""]
        else:
            lines += [
                "🔒 Настройка не завершена, мониторинг и письма не работают.",
                f"Осталось: {', '.join(status.missing_labels)}",
                "",
            ]

        lines += [f"📄 Резюме: {self._resume_status_text()}", ""]

        for field in PROFILE_FIELDS:
            value = profile.get(field.key)
            count = f" ({len(value)})" if field.kind == "list" and value else ""
            mark = " ⚠️" if field.key in status.incomplete_fields else ""
            lines.append(
                f"{field.emoji} {field.label}{count}: "
                f"{format_value(field, value)}{mark}"
            )

        lines.append("")
        lines.append("Нажми на поле, чтобы изменить. По этим данным пишутся письма.")

        buttons = [
            _button(f"{field.emoji} {field.label}", "e", field.key)
            for field in PROFILE_FIELDS
        ]
        rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
        rows.append([_button("📄 Загрузить резюме", "resume")])
        if not status.complete:
            rows.insert(0, [_button("▶️ Продолжить настройку", "setup")])

        return "\n".join(lines), InlineKeyboardMarkup(rows)

    def _resume_prompt_screen(self) -> Screen:
        text = (
            "📄 ЗАГРУЗКА РЕЗЮМЕ\n\n"
            f"{self._resume_status_text()}\n\n"
            "Пришли новое резюме одним PDF-файлом (до 10 МБ, с текстовым слоем). "
            "Старое будет заменено.\n\n"
            "Отмена: /cancel"
        )
        return text, InlineKeyboardMarkup([_back_row()])

    def _toggle_screen(
        self,
        field: ProfileField,
        user_data: dict,
        notice: str = "",
    ) -> Screen:
        wizard = is_onboarding(user_data)
        current = load_candidate_profile().get(field.key) or []
        selected = {item.casefold() for item in current}
        custom = [item for item in current if item.casefold() not in
                  {option.casefold() for option in field.options}]

        rows = [
            [
                _button(
                    ("✅ " if option.casefold() in selected else "⬜ ") + option,
                    "t",
                    field.key,
                    index,
                )
            ]
            for index, option in enumerate(field.options)
        ]
        if wizard:
            rows.append([_button("✔️ Готово", "next", field.key)])
        else:
            rows.append(_back_row("✔️ Готово"))

        text = f"{field.emoji} {field.label}\n\n{field.hint}"
        if wizard:
            text = f"{self._step_header(field.key)}\n\n{text}"
        if notice:
            text = f"{notice}\n\n{text}"
        if custom:
            text += f"\n\nДругие сохранённые значения: {', '.join(custom)}"

        return text, InlineKeyboardMarkup(rows)

    @staticmethod
    def _list_choice_screen(field: ProfileField) -> Screen:
        current = load_candidate_profile().get(field.key)
        text = (
            f"{field.emoji} {field.label}\n\n"
            f"Сейчас: {format_value(field, current, max_items=30)}\n\n"
            "Что сделать со списком?"
        )
        rows = [
            [
                _button("➕ Добавить", "o", field.key, "add"),
                _button("✏️ Заменить", "o", field.key, "rep"),
            ],
            [_button("🗑 Очистить", "c", field.key)],
            _back_row(),
        ]
        return text, InlineKeyboardMarkup(rows)

    @staticmethod
    def _input_prompt_screen(field: ProfileField, op: str) -> Screen:
        current = load_candidate_profile().get(field.key)

        if field.kind == "list":
            action = (
                "Пришли, что добавить (через запятую или с новой строки)."
                if op == "add"
                else "Пришли новый список целиком (через запятую или с новой строки). "
                "Старый будет заменён."
            )
        else:
            action = "Пришли новое значение."

        text = (
            f"{field.emoji} {field.label}\n\n"
            f"Сейчас: {format_value(field, current, max_items=30)}\n\n"
            f"{action}\n{field.hint}\n\n"
            "Отмена: /cancel"
        )

        rows = []
        if field.kind != "list":
            rows.append([_button("🗑 Очистить", "c", field.key)])
        rows.append(_back_row())

        return text, InlineKeyboardMarkup(rows)

    # ------------------------------------------------------------------
    # Пошаговая настройка
    # ------------------------------------------------------------------

    @staticmethod
    def _step_header(step: str) -> str:
        return f"🚀 НАСТРОЙКА · шаг {_step_number(step)} из {TOTAL_STEPS}"

    def _prepare_step(self, user_data: dict) -> Screen:
        """Экран следующего незавершённого шага. Заодно включает нужный режим ввода."""
        status = get_setup_status()
        step = status.next_step

        if step is None:
            cancel_onboarding(user_data)
            clear_mode(user_data)
            return self._done_screen()

        user_data[ONBOARDING_KEY] = True

        if step == RESUME_STEP:
            set_mode(user_data, MODE_RESUME)
            text = (
                f"{self._step_header(step)}\n\n"
                "📄 Резюме\n\n"
                "Пришли своё резюме одним PDF-файлом (до 10 МБ, с текстовым слоем). "
                "По нему будут писаться письма.\n\n"
                "Выйти из настройки: /cancel"
            )
            return text, None

        field = FIELDS_BY_KEY[step]

        if field.options:
            clear_mode(user_data)
            return self._toggle_screen(field, user_data)

        current = load_candidate_profile().get(field.key)
        set_mode(
            user_data,
            MODE_FIELD,
            field=field.key,
            op="rep" if field.kind == "list" else "set",
        )

        action = (
            "Пришли список через запятую или с новой строки."
            if field.kind == "list"
            else "Пришли значение."
        )
        text = (
            f"{self._step_header(step)}\n\n"
            f"{field.emoji} {field.label}\n\n"
            f"Сейчас: {format_value(field, current, max_items=30)}\n\n"
            f"{action}\n{field.hint}\n\n"
            "Выйти из настройки: /cancel"
        )

        rows = []
        if current:
            rows.append([_button("✅ Оставить как есть", "keep", field.key)])
        if field.skippable:
            rows.append([_button("⏭ Не важно / нет", "skip", field.key)])

        return text, InlineKeyboardMarkup(rows) if rows else None

    @staticmethod
    def _done_screen() -> Screen:
        text = (
            "🎉 Настройка завершена!\n\n"
            "Резюме загружено, профиль заполнен. Мониторинг вакансий запустится "
            "автоматически в течение минуты, перезапуск не нужен.\n\n"
            "✉️ Письмо под свою вакансию: /vacancy\n"
            "👤 Изменить данные: /profile"
        )
        return text, InlineKeyboardMarkup([[_button("👤 Открыть профиль", "show")]])

    async def send_setup_required(self, message: Message, status: SetupStatus):
        """Ответ на действие, недоступное до завершения настройки."""
        await message.reply_text(
            "🔒 Сначала нужно завершить настройку: загрузить резюме (PDF) "
            "и заполнить профиль. Пока это не сделано, мониторинг вакансий "
            "и письма не работают.\n\n"
            f"Осталось: {', '.join(status.missing_labels)}",
            reply_markup=InlineKeyboardMarkup(
                [[_button("▶️ Продолжить настройку", "setup")]]
            ),
        )

    async def setup_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        if not await self._guard(update):
            return

        clear_mode(context.user_data)
        text, markup = self._prepare_step(context.user_data)
        await update.effective_message.reply_text(text, reply_markup=markup)

    # ------------------------------------------------------------------
    # Команды
    # ------------------------------------------------------------------

    async def _guard(self, update: Update) -> bool:
        if self.is_admin(update):
            return True
        if update.effective_message:
            await update.effective_message.reply_text("⛔ Нет доступа.")
        return False

    async def profile_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        if not await self._guard(update):
            return

        clear_mode(context.user_data)
        text, markup = self._profile_screen()
        await update.effective_message.reply_text(text, reply_markup=markup)

    async def resume_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        if not await self._guard(update):
            return

        set_mode(context.user_data, MODE_RESUME)
        text, markup = self._resume_prompt_screen()
        await update.effective_message.reply_text(text, reply_markup=markup)

    # ------------------------------------------------------------------
    # Загрузка PDF
    # ------------------------------------------------------------------

    async def handle_document(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        message = update.effective_message
        mode = get_mode(context.user_data)

        if mode is None or mode["name"] != MODE_RESUME:
            await message.reply_text(
                "Если это резюме, сначала нажми /resume, а потом пришли файл. "
                "Вакансии в PDF я пока не читаю: пришли текст, ссылку или скриншот."
            )
            return

        document = message.document

        if document.file_size and document.file_size > MAX_RESUME_BYTES:
            await message.reply_text("⚠️ Файл больше 10 МБ. Пришли PDF поменьше.")
            return

        status = await message.reply_text("⏳ Читаю резюме…")

        try:
            telegram_file = await document.get_file()
            data = bytes(await telegram_file.download_as_bytearray())
            text, pages = await asyncio.to_thread(extract_resume_text_from_pdf, data)

        except ResumeError as error:
            # Режим сохраняем: можно сразу прислать другой файл.
            await self._safe_edit(status, f"⚠️ {error}")
            return

        except Exception:
            logger.exception("Не удалось обработать резюме")
            await self._safe_edit(
                status,
                "❌ Не получилось обработать файл. Попробуй ещё раз.",
            )
            return

        filename = document.file_name or "resume.pdf"
        save_resume(text, filename, pages)
        clear_mode(context.user_data)

        preview = " ".join(text[:RESUME_PREVIEW_CHARS].split())
        saved_text = (
            f"✅ Резюме загружено: {filename}\n"
            f"Страниц: {pages}, символов: {len(text)}\n\n"
            f"Начало текста (проверь, что прочитано корректно):\n«{preview}…»"
        )

        if is_onboarding(context.user_data):
            await self._safe_edit(status, saved_text)
            next_text, next_markup = self._prepare_step(context.user_data)
            await message.reply_text(next_text, reply_markup=next_markup)
            return

        await self._safe_edit(
            status,
            saved_text
            + "\n\nТеперь можно уточнить профиль: роли, навыки, желаемую зарплату и т. д.",
            InlineKeyboardMarkup([[_button("👤 Настроить профиль", "show")]]),
        )

    # ------------------------------------------------------------------
    # Ввод значения поля
    # ------------------------------------------------------------------

    async def handle_field_text(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        mode: dict,
    ):
        message = update.effective_message
        field = FIELDS_BY_KEY.get(mode.get("field"))

        if field is None:
            clear_mode(context.user_data)
            return

        try:
            value = parse_field_value(field, message.text or "")

            if field.kind == "list" and mode.get("op") == "add":
                current = load_candidate_profile().get(field.key) or []
                value = merge_lists(current, value)

            update_candidate_profile(field.key, value)

        except ProfileInputError as error:
            await message.reply_text(
                f"⚠️ {error}\n\nПопробуй ещё раз или нажми /cancel."
            )
            return

        except ValueError:
            logger.exception("Не удалось сохранить поле %s", field.key)
            await message.reply_text(
                "⚠️ Не получилось сохранить это значение. Проверь формат и попробуй ещё раз."
            )
            return

        clear_mode(context.user_data)

        await message.reply_text(f"✅ Сохранено: {field.label}")

        if is_onboarding(context.user_data):
            text, markup = self._prepare_step(context.user_data)
        else:
            text, markup = self._profile_screen()
        await message.reply_text(text, reply_markup=markup)

    # ------------------------------------------------------------------
    # Нажатия на кнопки
    # ------------------------------------------------------------------

    async def on_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        query = update.callback_query
        if query is None:
            return

        if not self.is_admin(update):
            await query.answer("⛔ Нет доступа.", show_alert=True)
            return

        await query.answer()

        parts = (query.data or "").split(":")[1:]
        action, args = (parts[0], parts[1:]) if parts else ("", [])

        try:
            await self._dispatch(query, context, action, args)
        except Exception:
            logger.exception("Ошибка обработки кнопки %s", query.data)
            await query.message.reply_text(
                "❌ Что-то пошло не так. Открой профиль заново: /profile"
            )

    async def _dispatch(self, query, context, action: str, args: list[str]):
        user_data = context.user_data

        if action in ("show", "back"):
            clear_mode(user_data)
            await self._edit(query, *self._profile_screen())
            return

        if action == "resume":
            set_mode(user_data, MODE_RESUME)
            await self._edit(query, *self._resume_prompt_screen())
            return

        if action == "setup":
            clear_mode(user_data)
            await self._edit(query, *self._prepare_step(user_data))
            return

        field = FIELDS_BY_KEY.get(args[0]) if args else None
        if field is None:
            return

        if action == "e":
            clear_mode(user_data)

            if field.options:
                await self._edit(query, *self._toggle_screen(field, user_data))
            elif field.kind == "list":
                await self._edit(query, *self._list_choice_screen(field))
            else:
                set_mode(user_data, MODE_FIELD, field=field.key, op="set")
                await self._edit(query, *self._input_prompt_screen(field, "set"))

        elif action == "o" and len(args) > 1 and args[1] in ("add", "rep"):
            set_mode(user_data, MODE_FIELD, field=field.key, op=args[1])
            await self._edit(query, *self._input_prompt_screen(field, args[1]))

        elif action == "c":
            clear_mode(user_data)
            update_candidate_profile(field.key, EMPTY_VALUES[field.kind])
            await self._edit(query, *self._profile_screen())

        elif action == "t" and len(args) > 1 and args[1].isdigit():
            await self._toggle_option(query, user_data, field, int(args[1]))

        elif action == "keep":
            # «Оставить как есть» доступно только для непустого значения.
            if load_candidate_profile().get(field.key):
                confirm_field(field.key)
            await self._edit(query, *self._prepare_step(user_data))

        elif action == "skip":
            if field.skippable:
                update_candidate_profile(field.key, EMPTY_VALUES[field.kind])
            await self._edit(query, *self._prepare_step(user_data))

        elif action == "next":
            if load_candidate_profile().get(field.key):
                confirm_field(field.key)
                await self._edit(query, *self._prepare_step(user_data))
            else:
                await self._edit(
                    query,
                    *self._toggle_screen(
                        field, user_data, notice="⚠️ Выбери хотя бы один вариант."
                    ),
                )

    async def _toggle_option(
        self,
        query,
        user_data: dict,
        field: ProfileField,
        index: int,
    ):
        if index >= len(field.options):
            return

        option = field.options[index]
        current = list(load_candidate_profile().get(field.key) or [])
        remaining = [item for item in current if item.casefold() != option.casefold()]

        if len(remaining) == len(current):
            remaining.append(option)

        update_candidate_profile(field.key, remaining)
        await self._edit(query, *self._toggle_screen(field, user_data))

    # ------------------------------------------------------------------

    @staticmethod
    async def _edit(query, text: str, markup: InlineKeyboardMarkup | None):
        try:
            await query.edit_message_text(text, reply_markup=markup)
        except Exception:
            # Например, «Message is not modified» при повторном нажатии.
            logger.debug("Не удалось обновить сообщение", exc_info=True)

    @staticmethod
    async def _safe_edit(
        message: Message,
        text: str,
        markup: InlineKeyboardMarkup | None = None,
    ):
        try:
            await message.edit_text(text, reply_markup=markup)
        except Exception:
            logger.debug("Не удалось обновить сообщение", exc_info=True)
