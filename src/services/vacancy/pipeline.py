import asyncio
import json
import logging
from pathlib import Path

from pypdf import PdfReader

from src.core import settings
from src.db import (
    init_db,
    is_notified,
    mark_as_notified,
    save_vacancy,
)
from src.integrations.telegram import send_vacancy
from src.services.cover_letter.service import CoverLetterService
from src.services.vacancy.identity import create_fingerprint
from src.services.vacancy.matcher import VacancyMatcher
from src.services.vacancy.parser import VacancyAnalyzer

logger = logging.getLogger(__name__)


class VacancyPipeline:
    def __init__(self):
        self.analyzer = VacancyAnalyzer()
        self.matcher = VacancyMatcher()
        self.cover_letter_service = CoverLetterService()
        self.candidate_profile = self._load_candidate_profile()
        self.resume_text = self._load_resume_text()
        init_db()

    @staticmethod
    def _load_candidate_profile() -> dict:
        path = Path(settings.CANDIDATE_PROFILE_PATH)
        if not path.exists():
            raise FileNotFoundError(f"Не найден профиль кандидата: {path}")

        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    @staticmethod
    def _load_resume_text() -> str:
        path = Path(settings.RESUME_PATH)
        if not path.exists():
            logger.warning(
                "Резюме не найдено (%s). Будет использован только профиль.", path
            )
            return ""

        try:
            reader = PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except Exception:
            logger.exception("Не удалось прочитать PDF-резюме")
            return ""

    async def process(
        self,
        vacancy_text: str,
        source: str = "manual_test",
    ) -> None:
        if not vacancy_text or not vacancy_text.strip():
            logger.info("Пустое сообщение. Пропускаем.")
            return

        # Анализ и оценка — синхронные вызовы LLM.
        vacancy = await asyncio.to_thread(self.analyzer.analyze, vacancy_text)

        if not vacancy.is_python_related:
            logger.info("Вакансия не связана с Python. Пропускаем.")
            return

        match = await asyncio.to_thread(self.matcher.match, vacancy)
        logger.info("Оценка вакансии: %s/100", match.score)

        if match.score < settings.VACANCY_MIN_SCORE or match.recommendation == "skip":
            logger.info("Вакансия не прошла фильтр соответствия.")
            return

        fingerprint = create_fingerprint(vacancy, vacancy_text)

        # Не запускаем повторную генерацию для уже отправленного уведомления.
        if is_notified(fingerprint):
            logger.info("Вакансия уже отправлялась: %s", fingerprint)
            return

        is_new = save_vacancy(
            fingerprint=fingerprint,
            vacancy_json=vacancy.model_dump_json(),
            match_json=match.model_dump_json(),
            score=match.score,
            recommendation=match.recommendation,
            source=source,
        )

        if not is_new:
            logger.info("Вакансия уже сохранена ранее; проверяем статус уведомления.")

        # Генерируем письмо и проверяем его. Никаких email-заявок/отправок.
        letter_result = await asyncio.to_thread(
            self.cover_letter_service.generate_checked,
            vacancy=vacancy,
            candidate_profile=self.candidate_profile,
            resume_text=self.resume_text,
            max_attempts=settings.COVER_LETTER_MAX_ATTEMPTS,
        )

        letter = letter_result.get("letter", "")
        review = letter_result.get("review", {})

        if not letter:
            logger.warning("Не удалось сгенерировать сопроводительное письмо.")
            return

        await send_vacancy(
            vacancy=vacancy,
            match=match,
            cover_letter=letter,
            review=review,
        )

        mark_as_notified(fingerprint)

        logger.info(
            "Вакансия и сопроводительное письмо отправлены в Telegram. "
            "Review passed: %s",
            letter_result.get("success", False),
        )
