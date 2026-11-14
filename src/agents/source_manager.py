import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from src.core import settings
from src.db import get_telegram_sources, set_telegram_source_active

logger = logging.getLogger(__name__)


class TelegramSourceManager:
    """
    Telegram Bot для управления источниками вакансий.

    Поддерживает просмотр, включение и отключение источников.
    Не отправляет email работодателям.
    """

    def __init__(self):
        self.application = (
            Application.builder()
            .token(settings.TELEGRAM_BOT_TOKEN.get_secret_value())
            .build()
        )

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

        await update.effective_message.reply_text(
            "👋 AI Job Hunter запущен!\n\n"
            "При запуске Telegram Listener автоматически "
            "ищет группы и каналы с Python-вакансиями.\n\n"
            "Найденные источники сохраняются в базе данных "
            "и подключаются для мониторинга.\n\n"
            "Новые источники дополнительно ищутся каждые 6 часов.\n\n"
            "Используй /sources для просмотра источников.\n\n"
            "Вакансии и сопроводительные письма будут приходить сюда.\n"
            "Отправка работодателям не выполняется."
        )

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
