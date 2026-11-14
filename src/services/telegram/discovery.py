import asyncio
import logging

from telethon import utils

from src.db import (
    init_db,
    save_telegram_source,
)
from src.services.telegram.classifier import TelegramSourceClassifier
from src.services.telegram.prefilter import may_contain_jobs

logger = logging.getLogger(__name__)


class TelegramSourceDiscovery:
    def __init__(self, client):
        self.client = client
        self.classifier = TelegramSourceClassifier()

    async def discover(self, dialog_limit: int = 200):
        init_db()

        dialogs = await self.client.get_dialogs(limit=dialog_limit)

        logger.info("Найдено доступных диалогов: %s", len(dialogs))

        for dialog in dialogs:
            entity = dialog.entity

            # Пропускаем личные диалоги и ботов.
            if not (dialog.is_group or dialog.is_channel):
                continue

            title = (
                getattr(entity, "title", None)
                or getattr(entity, "username", None)
                or str(entity.id)
            )

            username = getattr(entity, "username", None)

            logger.info("Изучаем источник: %s", title)

            try:
                classification = await self._analyze_source(
                    entity=entity,
                    title=title,
                )

                if classification is None:
                    continue

                # Активируем источник только при достаточно
                # уверенном подтверждении вакансий.
                active = (
                    classification.is_job_source and classification.confidence >= 75
                )

                chat_id = utils.get_peer_id(entity)

                save_telegram_source(
                    chat_id=chat_id,
                    title=title,
                    username=username,
                    category=classification.category,
                    confidence=classification.confidence,
                    is_job_source=classification.is_job_source,
                    python_jobs_present=classification.python_jobs_present,
                    active=active,
                )

                logger.info(
                    "%s | категория=%s | уверенность=%s | active=%s",
                    title,
                    classification.category,
                    classification.confidence,
                    active,
                )

            except Exception:
                logger.exception(
                    "Ошибка анализа источника: %s",
                    title,
                )

            # Не сканируем все группы одновременно:
            # это снижает нагрузку и риск FloodWait.
            await asyncio.sleep(1)

    async def _analyze_source(self, entity, title: str):
        samples = []
        candidate_count = 0

        # Берём до 100 последних сообщений.
        async for message in self.client.iter_messages(
            entity,
            limit=100,
        ):
            text = (message.message or "").strip()

            if not text:
                continue

            if may_contain_jobs(text):
                samples.append(text[:2000])
                candidate_count += 1

            if len(samples) >= 20:
                break

        # Нет даже потенциальных объявлений —
        # пока не тратим запрос к LLM.
        if candidate_count == 0:
            logger.info(
                "Потенциальных вакансий не найдено: %s",
                title,
            )
            return None

        return await asyncio.to_thread(
            self.classifier.classify,
            title,
            samples,
        )
