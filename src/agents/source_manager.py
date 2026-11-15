import asyncio
import logging

from telegram import BotCommand, BotCommandScopeChat, Message, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.agents.candidate_menu import (
    CALLBACK_PREFIX,
    MODE_FIELD,
    MODE_RESUME,
    MODE_VACANCY,
    CandidateMenu,
    cancel_onboarding,
    clear_mode,
    get_mode,
    is_onboarding,
    set_mode,
)
from src.core import settings
from src.db import get_telegram_sources, init_db, set_telegram_source_active
from src.integrations.telegram import (
    format_manual_review_note,
    format_manual_summary,
    split_telegram_text,
)
from src.services.candidate import get_setup_status
from src.services.vacancy.extractor import VacancyExtractionError
from src.services.vacancy.manual import ManualResult, ManualVacancyService

logger = logging.getLogger(__name__)

MAX_IMAGES_PER_VACANCY = 5
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MEDIA_GROUP_WAIT_SECONDS = 2.5
MAX_PARALLEL_REQUESTS = 2

BOT_COMMANDS = (
    BotCommand("setup", "Настройка: резюме и профиль"),
    BotCommand("vacancy", "Письмо под вакансию"),
    BotCommand("resume", "Загрузить резюме (PDF)"),
    BotCommand("profile", "Профиль и предпочтения"),
    BotCommand("sources", "Источники вакансий"),
    BotCommand("cancel", "Отменить текущее действие"),
    BotCommand("help", "Справка"),
)


class TelegramSourceManager:
    """
    Telegram Bot для управления источниками вакансий.

    Поддерживает просмотр, включение и отключение источников,
    а также ручную отправку вакансии (текст, ссылка, скриншот),
    в ответ на которую бот пишет сопроводительное письмо.
    Не отправляет email работодателям.
    """

    def __init__(self):
        self.application = (
            Application.builder()
            .token(settings.TELEGRAM_BOT_TOKEN.get_secret_value())
            # LLM-запросы длятся десятки секунд: без параллельной обработки
            # бот не отвечал бы на команды, пока готовится письмо.
            .concurrent_updates(True)
            .post_init(self._post_init)
            .build()
        )

        init_db()
        self.menu = CandidateMenu(self.is_admin)

        self._service: ManualVacancyService | None = None
        self._request_slots = asyncio.Semaphore(MAX_PARALLEL_REQUESTS)
        self._media_groups: dict[str, dict] = {}

        self._register_handlers()

    def _register_handlers(self):
        self.application.add_handler(
            CommandHandler("start", self.start)
        )

        self.application.add_handler(
            CommandHandler("help", self.help_command)
        )

        self.application.add_handler(
            CommandHandler("sources", self.sources)
        )

        self.application.add_handler(
            CommandHandler("enable", self.enable_source)
        )

        self.application.add_handler(
            CommandHandler("disable", self.disable_source)
        )

        self.application.add_handler(
            CommandHandler("setup", self.menu.setup_command)
        )

        self.application.add_handler(
            CommandHandler("vacancy", self.vacancy_command)
        )

        self.application.add_handler(
            CommandHandler("cancel", self.cancel_command)
        )

        self.application.add_handler(
            CommandHandler("profile", self.menu.profile_command)
        )

        self.application.add_handler(
            CommandHandler("resume", self.menu.resume_command)
        )

        self.application.add_handler(
            CallbackQueryHandler(
                self.menu.on_callback,
                pattern=f"^{CALLBACK_PREFIX}:",
            )
        )

        # Вакансии принимаем только от администратора в личном чате:
        # чужие сообщения игнорируются и не тратят токены LLM.
        admin_chat = (
            filters.User(user_id=settings.TELEGRAM_ADMIN_USER_ID)
            & filters.ChatType.PRIVATE
        )

        self.application.add_handler(
            MessageHandler(
                admin_chat & filters.TEXT & ~filters.COMMAND,
                self.handle_text,
            )
        )

        self.application.add_handler(
            MessageHandler(
                admin_chat & (filters.PHOTO | filters.Document.IMAGE),
                self.handle_image,
            )
        )

        self.application.add_handler(
            MessageHandler(
                admin_chat & filters.Document.PDF,
                self.menu.handle_document,
            )
        )

        self.application.add_error_handler(self.on_error)

    async def _setup_complete_or_prompt(self, message: Message) -> bool:
        """Письма и мониторинг доступны только после настройки."""
        status = get_setup_status()

        if status.complete:
            return True

        await self.menu.send_setup_required(message, status)
        return False

    async def _post_init(self, application: Application):
        """Показывает команды в меню Telegram — только администратору."""
        try:
            await application.bot.set_my_commands(
                BOT_COMMANDS,
                scope=BotCommandScopeChat(chat_id=settings.TELEGRAM_ADMIN_USER_ID),
            )
        except Exception:
            logger.warning(
                "Не удалось зарегистрировать меню команд. "
                "Команды работают и без него.",
                exc_info=True,
            )

    @property
    def service(self) -> ManualVacancyService:
        # Создаём при первом использовании, чтобы проблемы с профилем
        # или резюме не мешали работе команд /sources, /enable, /disable.
        if self._service is None:
            self._service = ManualVacancyService()
        return self._service

    def is_admin(self, update: Update) -> bool:
        user = update.effective_user

        return (
            user is not None
            and user.id == settings.TELEGRAM_ADMIN_USER_ID
        )

    async def start(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        if not self.is_admin(update):
            if update.effective_message:
                await update.effective_message.reply_text(
                    "⛔ Нет доступа."
                )
            return

        if not get_setup_status().complete:
            await update.effective_message.reply_text(
                "👋 Привет! Это AI Job Hunter.\n\n"
                "🔒 Пока ты не загрузишь резюме (PDF) и не заполнишь профиль, "
                "мониторинг вакансий и письма не запускаются. "
                "Это займёт пару минут. Начинаем."
            )
            await self.menu.setup_command(update, context)
            return

        await update.effective_message.reply_text(
            "👋 AI Job Hunter запущен!\n\n"
            "При запуске Telegram Listener автоматически "
            "ищет группы и каналы с Python-вакансиями.\n\n"
            "Найденные источники сохраняются в базе данных "
            "и подключаются для мониторинга.\n\n"
            "Новые источники дополнительно ищутся каждые 6 часов.\n\n"
            "Используй /sources для просмотра источников.\n\n"
            "Вакансии и сопроводительные письма будут приходить сюда.\n\n"
            "✉️ Письмо под конкретную вакансию: нажми /vacancy "
            "и пришли её текстом, ссылкой или скриншотом.\n"
            "📄 Своё резюме (PDF): /resume\n"
            "👤 Профиль и предпочтения: /profile\n\n"
            "Отправка работодателям не выполняется."
        )

    async def vacancy_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        if not self.is_admin(update):
            await update.effective_message.reply_text("⛔ Нет доступа.")
            return

        if not await self._setup_complete_or_prompt(update.effective_message):
            return

        set_mode(context.user_data, MODE_VACANCY)

        await update.effective_message.reply_text(
            "✉️ Пришли вакансию, и я напишу сопроводительное письмо.\n\n"
            "Подойдёт текст, ссылка или скриншот (можно несколько сразу).\n"
            "Отмена: /cancel"
        )

    async def cancel_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        if not self.is_admin(update):
            await update.effective_message.reply_text("⛔ Нет доступа.")
            return

        had_mode = (
            get_mode(context.user_data) is not None
            or is_onboarding(context.user_data)
        )
        clear_mode(context.user_data)
        cancel_onboarding(context.user_data)

        if not had_mode:
            await update.effective_message.reply_text("Отменять нечего.")
            return

        hint = (
            "\n\nНастройка не завершена, вернуться к ней: /setup"
            if not get_setup_status().complete
            else ""
        )
        await update.effective_message.reply_text(f"Отменено.{hint}")

    async def help_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        if not self.is_admin(update):
            await update.effective_message.reply_text(
                "⛔ Нет доступа."
            )
            return

        await update.effective_message.reply_text(
            "🤖 AI Job Hunter — команды\n\n"
            "УПРАВЛЕНИЕ ИСТОЧНИКАМИ:\n"
            "/sources — список Telegram-источников\n"
            "/enable ID — включить источник\n"
            "/disable ID — отключить источник\n\n"
            "АВТОМАТИЧЕСКИЙ ПОИСК:\n"
            "При запуске Listener выполняет поиск групп и каналов.\n"
            "Повторный поиск выполняется каждые 6 часов.\n\n"
            "Listener обновляет список активных источников "
            "каждые 30 секунд.\n\n"
            "Бот присылает подходящие вакансии, оценку соответствия "
            "и подготовленное сопроводительное письмо.\n\n"
            "ПИСЬМО ПОД СВОЮ ВАКАНСИЮ:\n"
            "/vacancy — затем пришли вакансию текстом, ссылкой "
            "или скриншотом (можно несколько фото сразу). "
            "Команду можно пропустить: любое сообщение с вакансией "
            "обрабатывается так же.\n\n"
            "РЕЗЮМЕ И ПРОФИЛЬ:\n"
            "/setup — пошаговая настройка (резюме и профиль). "
            "Пока она не завершена, мониторинг и письма не работают\n"
            "/resume — загрузить своё резюме (PDF)\n"
            "/profile — роли, навыки, опыт, формат работы, зарплата, "
            "языки, локации и пожелания\n"
            "/cancel — отменить текущее действие\n\n"
            "Автоматическая отправка работодателям не выполняется."
        )

    async def sources(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        if not self.is_admin(update):
            await update.effective_message.reply_text(
                "⛔ Нет доступа."
            )
            return

        sources = get_telegram_sources()

        if not sources:
            await update.effective_message.reply_text(
                "📭 Источники пока не найдены.\n\n"
                "Проверь, что Telegram Listener запущен.\n"
                "Он автоматически ищет группы и каналы "
                "при старте и повторяет поиск каждые 6 часов."
            )
            return

        lines = ["📚 TELEGRAM ИСТОЧНИКИ\n"]

        for source in sources[:30]:
            status = (
                "🟢 Активен"
                if source["active"]
                else "🔴 Отключён"
            )

            title = source["title"] or "Без названия"
            category = source["category"] or "Не определена"
            confidence = source["confidence"] or 0

            lines.append(
                f"📌 {title}\n"
                f"ID: {source['chat_id']}\n"
                f"Категория: {category}\n"
                f"Уверенность: {confidence}%\n"
                f"Статус: {status}\n"
            )

        lines.append(
            "\nКоманды:\n"
            "/enable ID — включить источник\n"
            "/disable ID — отключить источник"
        )

        await update.effective_message.reply_text(
            "\n".join(lines)
        )

    async def enable_source(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        if not self.is_admin(update):
            await update.effective_message.reply_text(
                "⛔ Нет доступа."
            )
            return

        await self._set_source(
            update,
            context,
            active=True,
        )

    async def disable_source(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        if not self.is_admin(update):
            await update.effective_message.reply_text(
                "⛔ Нет доступа."
            )
            return

        await self._set_source(
            update,
            context,
            active=False,
        )

    async def _set_source(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        active: bool,
    ):
        action = "enable" if active else "disable"

        if len(context.args) != 1:
            await update.effective_message.reply_text(
                f"Использование: /{action} -100123456789"
            )
            return

        try:
            chat_id = int(context.args[0])

        except ValueError:
            await update.effective_message.reply_text(
                "ID источника должен быть числом."
            )
            return

        updated = set_telegram_source_active(
            chat_id,
            active,
        )

        if updated:
            state = (
                "включён 🟢"
                if active
                else "отключён 🔴"
            )

            await update.effective_message.reply_text(
                f"Источник {chat_id} {state}.\n\n"
                "Listener автоматически применит изменение "
                "в течение 30 секунд. Перезапуск не требуется."
            )

        else:
            await update.effective_message.reply_text(
                "Источник не найден в базе данных."
            )

    # ------------------------------------------------------------------
    # Ручная отправка вакансии
    # ------------------------------------------------------------------

    async def handle_text(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        message = update.effective_message
        mode = get_mode(context.user_data)

        if mode and mode["name"] == MODE_FIELD:
            await self.menu.handle_field_text(update, context, mode)
            return

        if mode and mode["name"] == MODE_RESUME:
            await message.reply_text("Жду резюме одним PDF-файлом. Отмена: /cancel")
            return

        # Режим /vacancy одноразовый. Вакансию без команды тоже принимаем.
        clear_mode(context.user_data)

        text = (message.text or "").strip()

        if not text:
            return

        if not await self._setup_complete_or_prompt(message):
            return

        await self._respond_with_letter(
            message,
            lambda progress: self.service.from_text(text, progress),
        )

    async def handle_image(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        message = update.effective_message
        mode = get_mode(context.user_data)

        if mode and mode["name"] in (MODE_FIELD, MODE_RESUME):
            expected = (
                "резюме одним PDF-файлом"
                if mode["name"] == MODE_RESUME
                else "значение текстом"
            )
            await message.reply_text(f"Сейчас жду {expected}. Отмена: /cancel")
            return

        clear_mode(context.user_data)

        if not await self._setup_complete_or_prompt(message):
            return

        group_id = message.media_group_id

        # Альбом приходит отдельными сообщениями. Первое из них становится
        # «ведущим» и собирает остальные; регистрация идёт до первого await,
        # поэтому два сообщения не смогут стать ведущими одновременно.
        leader = False
        entry = None

        if group_id is not None:
            entry = self._media_groups.get(group_id)
            if entry is None:
                entry = {"images": [], "caption": "", "pending": 0}
                self._media_groups[group_id] = entry
                leader = True
            entry["pending"] += 1

        try:
            image = await self._download_image(message)
        except VacancyExtractionError as error:
            if entry is not None:
                entry["pending"] -= 1
            if leader:
                self._media_groups.pop(group_id, None)
            await message.reply_text(f"⚠️ {error}")
            return
        except Exception:
            logger.exception("Не удалось скачать изображение")
            if entry is not None:
                entry["pending"] -= 1
            if leader:
                self._media_groups.pop(group_id, None)
            await message.reply_text(
                "❌ Не получилось скачать изображение. Попробуй ещё раз."
            )
            return

        caption = message.caption or ""

        if entry is None:
            images = [image]
        else:
            entry["images"].append((message.message_id, *image))
            entry["pending"] -= 1
            if caption:
                entry["caption"] = caption

            if not leader:
                return

            await asyncio.sleep(MEDIA_GROUP_WAIT_SECONDS)
            while entry["pending"] > 0:
                await asyncio.sleep(0.5)

            self._media_groups.pop(group_id, None)

            ordered = sorted(entry["images"], key=lambda item: item[0])
            images = [(data, mime) for _, data, mime in ordered]
            caption = entry["caption"]

        truncated = len(images) > MAX_IMAGES_PER_VACANCY
        images = images[:MAX_IMAGES_PER_VACANCY]

        if truncated:
            await message.reply_text(
                f"ℹ️ Беру только первые {MAX_IMAGES_PER_VACANCY} изображений."
            )

        await self._respond_with_letter(
            message,
            lambda progress: self.service.from_images(images, caption, progress),
        )

    @staticmethod
    async def _download_image(message: Message) -> tuple[bytes, str]:
        if message.photo:
            # Последний элемент — самое большое разрешение.
            telegram_file = await message.photo[-1].get_file()
            mime_type = "image/jpeg"
        else:
            document = message.document
            if document.file_size and document.file_size > MAX_IMAGE_BYTES:
                raise VacancyExtractionError(
                    "Файл слишком большой (больше 10 МБ). Пришли скриншот поменьше."
                )
            telegram_file = await document.get_file()
            mime_type = document.mime_type or "image/jpeg"

        data = bytes(await telegram_file.download_as_bytearray())
        return data, mime_type

    async def _respond_with_letter(self, message: Message, build_task):
        """Общий сценарий: статус → обработка → итог, письмо и проверка."""
        status = await message.reply_text("⏳ Принято, начинаю обработку…")

        async def progress(text: str):
            await self._safe_edit(status, text)

        async with self._request_slots:
            typing_task = asyncio.create_task(self._keep_typing(message))

            try:
                result = await build_task(progress)

            except VacancyExtractionError as error:
                await self._safe_edit(status, f"⚠️ {error}")
                return

            except Exception:
                logger.exception("Ошибка при подготовке письма")
                await self._safe_edit(
                    status,
                    "❌ Не удалось подготовить письмо. "
                    "Возможно, временная ошибка AI-сервиса — попробуй ещё раз "
                    "через минуту.",
                )
                return

            finally:
                typing_task.cancel()

        await self._send_result(message, status, result)

    async def _send_result(
        self,
        message: Message,
        status: Message,
        result: ManualResult,
    ):
        await self._safe_edit(
            status,
            format_manual_summary(result.vacancy, result.match),
        )

        if not result.letter:
            await message.reply_text(
                "❌ Не удалось сгенерировать письмо. Попробуй отправить вакансию ещё раз."
            )
            return

        # Письмо отдельным сообщением — его удобно скопировать целиком.
        for chunk in split_telegram_text(result.letter):
            await message.reply_text(chunk)

        await message.reply_text(
            format_manual_review_note(result.review, result.review_passed)
        )

    @staticmethod
    async def _keep_typing(message: Message):
        """Показывает «печатает…», пока идёт обработка."""
        try:
            while True:
                await message.chat.send_action(ChatAction.TYPING)
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Не удалось отправить chat action", exc_info=True)

    @staticmethod
    async def _safe_edit(status: Message, text: str):
        try:
            await status.edit_text(text)
        except Exception:
            # Например, «Message is not modified» — не критично.
            logger.debug("Не удалось обновить статус", exc_info=True)

    async def on_error(
        self,
        update: object,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        logger.error(
            "Необработанная ошибка в обработчике",
            exc_info=context.error,
        )

    def run(self):
        logger.info("Starting Telegram Source Manager...")

        self.application.run_polling(
            allowed_updates=Update.ALL_TYPES
        )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
    )

    TelegramSourceManager().run()
