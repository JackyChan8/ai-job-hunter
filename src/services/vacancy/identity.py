import hashlib
import re

from src.services.vacancy.schemas import VacancyData


def create_fingerprint(
    vacancy: VacancyData,
    raw_text: str,
) -> str:
    """
    Создаёт стабильный идентификатор вакансии.

    Если есть ссылка на отклик — используем её.
    Иначе используем компанию, должность и текст вакансии.
    """

    if vacancy.application_url:
        identity = vacancy.application_url.strip().lower()
    else:
        normalized_text = re.sub(r"\s+", " ", raw_text.lower()).strip()

        identity = "|".join(
            [
                vacancy.company or "",
                vacancy.title or "",
                normalized_text,
            ]
        )

    return hashlib.sha256(identity.encode("utf-8")).hexdigest()
