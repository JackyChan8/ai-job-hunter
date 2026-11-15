import asyncio
import logging

from telethon import TelegramClient, events, utils

from src.core import settings
from src.db import (
    get_active_telegram_sources,
    init_db,
    save_telegram_source,
)
from src.services.candidate import get_setup_status
from src.services.vacancy.pipeline import VacancyPipeline

logger = logging.getLogger(__name__)


class TelegramVacancyListener:
    SOURCE_REFRESH_INTERVAL = 30
    SETUP_CHECK_INTERVAL = 30
    DISCOVERY_INTERVAL = 6 * 60 * 60

    MAX_DIALOGS_TO_SCAN = 500
    MESSAGES_TO_SCAN = 20

    JOB_KEYWORDS = (
        "job",
        "jobs",
        "vacancy",
        "vacancies",
        "hiring",
        "recruitment",
        "career",
        "careers",
        "opportunity",
        "opportunities",
        "developer",
        "engineer",
        "работа",
        "вакансия",
        "вакансии",
        "вакансий",
        "ищем",
        "требуется",
        "разработчик",
        "программист",
        "найм",
    )

    PYTHON_KEYWORDS = (
        "python",
        "django",
        "fastapi",
        "flask",
        "sqlalchemy",
        "pydantic",
        "celery",
    )

    def __init__(self):
        self.client = TelegramClient(
            settings.TELEGRAM_SESSION_PATH,
            settings.TELEGRAM_API_ID,
            settings.TELEGRAM_API_HASH.get_secret_value(),
        )

        self.pipeline = VacancyPipeline()

        self.source_entities = {}
        self.event_builder = None

        self.refresh_task = None
        self.discovery_task = None

    @staticmethod
    def _find_keywords(text: str, keywords: tuple) -> list:
        text = text.lower()

        return [
            keyword
            for keyword in keywords
            if keyword in text
        ]

    async def discover_sources(self):
        """
        Автоматически ищет Telegram-группы и каналы,
        доступные авторизованному аккаунту.

        Анализирует названия и последние сообщения.
        Релевантные источники сохраняет в SQLite.
        """

        logger.info("Начинаем автоматический поиск Telegram-источников.")

        scanned = 0
        discovered = 0
        activated = 0

        async for dialog in self.client.iter_dialogs(
            limit=self.MAX_DIALOGS_TO_SCAN
        ):
            entity = dialog.entity

            # Пропускаем личные чаты и ботов.
            if not (
                getattr(dialog, "is_group", False)
                or getattr(dialog, "is_channel", False)
            ):
                continue

            title = getattr(entity, "title", None)

            if not title:
                continue

            chat_id = utils.get_peer_id(entity)
            username = getattr(entity, "username", None)

            scanned += 1

            try:
                messages = await self.client.get_messages(
                    entity,
                    limit=self.MESSAGES_TO_SCAN,
                )

                messages_text = "\n".join(
                    message.message
                    for message in messages
                    if message.message
                )

                combined_text = f"{title}\n{messages_text}"

                title_job_keywords = self._find_keywords(
                    title,
                    self.JOB_KEYWORDS,
                )

                message_job_keywords = self._find_keywords(
                    messages_text,
                    self.JOB_KEYWORDS,
                )

                title_python_keywords = self._find_keywords(
                    title,
                    self.PYTHON_KEYWORDS,
                )

                message_python_keywords = self._find_keywords(
                    messages_text,
                    self.PYTHON_KEYWORDS,
                )

                # Оценка релевантности источника.
                job_score = min(
                    60,
                    len(title_job_keywords) * 25
                    + len(message_job_keywords) * 10,
                )

                python_score = min(
                    40,
                    len(title_python_keywords) * 20
                    + len(message_python_keywords) * 10,
                )

                confidence = min(
                    100,
                    job_score + python_score,
                )

                is_job_source = job_score >= 25

                python_jobs_present = (
                    bool(title_python_keywords or message_python_keywords)
                    and is_job_source
                )

                # Автоматически активируем только источники,
                # в которых есть признаки вакансий Python.
                active = (
                    is_job_source
                    and python_jobs_present
                    and confidence >= 40
                )

                if not is_job_source:
                    continue

                category = (
                    "python_jobs"
                    if python_jobs_present
                    else "it_jobs"
                )

                save_telegram_source(
                    chat_id=chat_id,
                    title=title,
                    username=username,
                    category=category,
                    confidence=confidence,
                    is_job_source=is_job_source,
                    python_jobs_present=python_jobs_present,
                    active=active,
                )

                discovered += 1

                if active:
                    activated += 1

                    logger.info(
                        "Найден Python job-источник: %s | "
                        "ID: %s | Уверенность: %s%%",
                        title,
                        chat_id,
                        confidence,
                    )
                else:
                    logger.info(
                        "Найден IT-источник: %s | "
                        "ID: %s | Уверенность: %s%%",
                        title,
                        chat_id,
                        confidence,
                    )

                # Небольшая пауза между запросами.
                await asyncio.sleep(0.1)

            except Exception:
                logger.exception(
                    "Ошибка анализа Telegram-источника: %s",
                    title,
                )

        logger.info(
            "Поиск источников завершён. "
            "Проверено групп и каналов: %s. "
            "Найдено релевантных: %s. "
            "Активировано: %s.",
            scanned,
            discovered,
            activated,
        )

    async def load_sources(self):
        """
        Синхронизирует активные источники из SQLite
        с обработчиками Telegram Listener.
        """

        sources = get_active_telegram_sources()

        new_entities = {}

        for source in sources:
            chat_id = int(source["chat_id"])

            # Уже подключённые источники повторно не загружаем.
            if chat_id in self.source_entities:
                new_entities[chat_id] = self.source_entities[chat_id]
                continue

            try:
                entity = await self.client.get_entity(chat_id)

                new_entities[chat_id] = entity

                logger.info(
                    "Источник подключён: %s (%s)",
                    source["title"],
                    chat_id,
                )

            except Exception:
                logger.exception(
                    "Не удалось подключить источник: %s",
                    source["title"],
                )

        old_ids = set(self.source_entities)
        new_ids = set(new_entities)

        if old_ids == new_ids:
            return

        if self.event_builder is not None:
            self.client.remove_event_handler(
                self.handle_new_message,
                self.event_builder,
            )

            self.event_builder = None

        self.source_entities = new_entities

        if self.source_entities:
            self.event_builder = events.NewMessage(
                chats=list(self.source_entities.values())
            )

            self.client.add_event_handler(
                self.handle_new_message,
                self.event_builder,
            )

        added = new_ids - old_ids
        removed = old_ids - new_ids

        logger.info(
            "Список источников обновлён. "
            "Всего: %s. Добавлено: %s. Удалено: %s.",
            len(new_ids),
            len(added),
            len(removed),
        )

        if not new_entities:
            logger.warning(
                "Нет активных источников. "
                "Listener продолжает ожидание."
            )

    async def source_refresh_loop(self):
        """
        Обновляет активные источники каждые 30 секунд.
        """

        while True:
            try:
                await asyncio.sleep(
                    self.SOURCE_REFRESH_INTERVAL
                )

                await self.load_sources()

            except asyncio.CancelledError:
                raise

            except Exception:
                logger.exception(
                    "Ошибка обновления списка источников."
                )

    async def discovery_loop(self):
        """
        Повторяет обнаружение источников каждые 6 часов.
        """

        while True:
            try:
                await asyncio.sleep(
                    self.DISCOVERY_INTERVAL
                )

                await self.discover_sources()
                await self.load_sources()

            except asyncio.CancelledError:
                raise

            except Exception:
                logger.exception(
                    "Ошибка автоматического поиска источников."
                )

    async def handle_new_message(self, event):
        message = event.message

        if not message or not message.message:
            return

        text = message.message.strip()

        if not text:
            return

        chat = await event.get_chat()

        chat_title = (
            getattr(chat, "title", None)
            or getattr(chat, "username", None)
            or str(event.chat_id)
        )

        source = f"telegram:{chat_title}:{event.chat_id}"

        logger.info(
            "Новое сообщение из %s",
            chat_title,
        )

        try:
            await self.pipeline.process(
                vacancy_text=text,
                source=source,
            )

        except Exception:
            logger.exception(
                "Ошибка обработки сообщения из %s",
                chat_title,
            )

    async def wait_for_setup(self):
        """Ничего не запускаем, пока в боте не загружено резюме
        и не заполнен профиль. Проверка повторяется автоматически."""

        announced = False

        while True:
            status = get_setup_status()

            if status.complete:
                break

            if not announced:
                logger.warning(
                    "Настройка не завершена, Listener ожидает. "
                    "Открой бота и выполни /setup. Осталось: %s.",
                    ", ".join(status.missing_labels),
                )
                announced = True

            await asyncio.sleep(self.SETUP_CHECK_INTERVAL)

        if announced:
            logger.info("Настройка завершена. Запускаем Listener.")

    async def start(self):
        init_db()

        await self.wait_for_setup()

        await self.client.connect()

        if not await self.client.is_user_authorized():
            raise RuntimeError(
                "Telegram-аккаунт не авторизован. "
                "Сначала выполни: "
                "make auth-telegram"
            )

        me = await self.client.get_me()

        logger.info(
            "Авторизован Telegram-аккаунт: %s",
            me.username or me.id,
        )

        # Главный шаг: поиск источников сразу при запуске.
        await self.discover_sources()

        # Подключаем найденные активные источники.
        await self.load_sources()

        # Запускаем периодическое обновление.
        self.refresh_task = asyncio.create_task(
            self.source_refresh_loop()
        )

        self.discovery_task = asyncio.create_task(
            self.discovery_loop()
        )

        logger.info(
            "Telegram Listener запущен. "
            "Обновление источников: каждые %s секунд. "
            "Повторный Discovery: каждые %s часов.",
            self.SOURCE_REFRESH_INTERVAL,
            self.DISCOVERY_INTERVAL // 3600,
        )

        try:
            await self.client.run_until_disconnected()

        finally:
            for task in (
                self.refresh_task,
                self.discovery_task,
            ):
                if task:
                    task.cancel()

                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            if self.client.is_connected():
                await self.client.disconnect()
