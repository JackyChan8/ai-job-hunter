import asyncio

from src.services.vacancy.pipeline import VacancyPipeline


async def main():
    vacancy_text = """
    Python Backend Developer

    Компания: Example Tech

    Ищем Python-разработчика в команду backend-разработки.

    Требования:
    - Опыт Python от 2 лет
    - Знание FastAPI
    - Опыт работы с PostgreSQL
    - Знание Docker
    - Понимание REST API

    Условия:
    - Полностью удалённая работа
    - Зарплата: от 150 000 рублей
    - Полная занятость

    Для отклика:
    hr@example.com
    """

    pipeline = VacancyPipeline()

    await pipeline.process(
        vacancy_text=vacancy_text,
        source="manual_test",
    )


if __name__ == "__main__":
    asyncio.run(main())
