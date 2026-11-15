"""Ручная отправка вакансии боту: текст, ссылка или скриншоты.

В отличие от автоматического пайплайна здесь нет фильтра по баллу:
человек сам прислал вакансию и хочет получить письмо в любом случае.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from src.core import settings
from src.db import init_db, mark_as_notified, save_vacancy
from src.services.candidate import load_candidate_profile, load_resume_text
from src.services.cover_letter.service import CoverLetterService
from src.services.vacancy.extractor import (
    VacancyExtractionError,
    extract_text_from_images,
    fetch_vacancy_text,
    find_urls,
    is_link_request,
)
from src.services.vacancy.identity import create_fingerprint
from src.services.vacancy.matcher import VacancyMatch, VacancyMatcher
from src.services.vacancy.parser import VacancyAnalyzer
from src.services.vacancy.schemas import VacancyData

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]


@dataclass
class ManualResult:
    vacancy: VacancyData
    match: VacancyMatch | None
    letter: str
    review: dict = field(default_factory=dict)
    review_passed: bool = False


def detect_letter_language(text: str) -> str:
    """Язык письма — как у вакансии (в падеже для фразы «на ... языке»).

    Английский выбираем, только если кириллицы почти нет: русские вакансии
    часто густо насыщены названиями технологий на латинице."""
    letters = [char.lower() for char in text if char.isalpha()]
    if not letters:
        return "русском"

    cyrillic = sum("а" <= char <= "я" or char == "ё" for char in letters)
    return "русском" if cyrillic / len(letters) >= 0.1 else "английском"


class ManualVacancyService:
    def __init__(self):
        self.analyzer = VacancyAnalyzer()
        self.matcher = VacancyMatcher()
        self.cover_letter_service = CoverLetterService()
        init_db()

    async def from_text(
        self,
        text: str,
        progress: ProgressCallback | None = None,
    ) -> ManualResult:
        """Принимает либо текст вакансии, либо сообщение со ссылкой."""
        text = text.strip()

        if is_link_request(text):
            url = find_urls(text)[0]
            await self._report(progress, "🔗 Открываю ссылку…")
            vacancy_text = await asyncio.to_thread(fetch_vacancy_text, url)
            source = f"manual:link:{url}"
        else:
            vacancy_text = text
            source = "manual:text"

        return await self._process(vacancy_text, source, progress)

    async def from_images(
        self,
        images: list[tuple[bytes, str]],
        caption: str = "",
        progress: ProgressCallback | None = None,
    ) -> ManualResult:
        await self._report(progress, "🖼 Распознаю текст на изображении…")
        vacancy_text = await asyncio.to_thread(
            extract_text_from_images,
            images,
            caption,
        )
        return await self._process(vacancy_text, "manual:image", progress)

    async def _process(
        self,
        vacancy_text: str,
        source: str,
        progress: ProgressCallback | None,
    ) -> ManualResult:
        await self._report(progress, "🔎 Анализирую вакансию…")
        vacancy = await asyncio.to_thread(self.analyzer.analyze, vacancy_text)

        if not (vacancy.title or vacancy.requirements or vacancy.responsibilities):
            raise VacancyExtractionError(
                "Не похоже на описание вакансии — не нашла ни должности, "
                "ни требований, ни обязанностей."
            )

        match = None
        try:
            match = await asyncio.to_thread(self.matcher.match, vacancy)
        except Exception:
            # Оценка — дополнительная информация, письмо важнее.
            logger.exception("Не удалось оценить соответствие вакансии")

        await self._report(progress, "✍️ Пишу сопроводительное письмо…")
        letter_result = await asyncio.to_thread(
            self.cover_letter_service.generate_checked,
            vacancy=vacancy,
            # Читаем при каждом запросе: резюме и профиль можно менять в боте.
            candidate_profile=load_candidate_profile(),
            resume_text=load_resume_text(),
            max_attempts=settings.COVER_LETTER_MAX_ATTEMPTS,
            language=detect_letter_language(vacancy_text),
        )

        self._remember(vacancy, match, vacancy_text, source)

        return ManualResult(
            vacancy=vacancy,
            match=match,
            letter=(letter_result.get("letter") or "").strip(),
            review=letter_result.get("review") or {},
            review_passed=bool(letter_result.get("success")),
        )

    @staticmethod
    def _remember(
        vacancy: VacancyData,
        match: VacancyMatch | None,
        vacancy_text: str,
        source: str,
    ) -> None:
        """Сохраняет вакансию и помечает отправленной, чтобы Listener
        не прислал ту же вакансию повторно. Сбой БД не должен ломать ответ."""
        if match is None:
            return

        try:
            fingerprint = create_fingerprint(vacancy, vacancy_text)
            save_vacancy(
                fingerprint=fingerprint,
                vacancy_json=vacancy.model_dump_json(),
                match_json=match.model_dump_json(),
                score=match.score,
                recommendation=match.recommendation,
                source=source,
            )
            mark_as_notified(fingerprint)
        except Exception:
            logger.exception("Не удалось сохранить вакансию в БД")

    @staticmethod
    async def _report(progress: ProgressCallback | None, text: str) -> None:
        if progress is None:
            return
        try:
            await progress(text)
        except Exception:
            logger.debug("Не удалось обновить статус", exc_info=True)
