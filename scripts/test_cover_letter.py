import json

from src.services.cover_letter.service import CoverLetterService
from src.services.vacancy.schemas import VacancyData


def main():

    vacancy = VacancyData(
        title="Python Developer",
        company="Example Tech",
        location="Удалённо",
        salary="150 000 рублей",
        requirements=[
            "Python",
            "FastAPI",
            "PostgreSQL",
            "Docker",
        ],
        technologies=[
            "Python",
            "FastAPI",
            "PostgreSQL",
            "Docker",
        ],
        responsibilities=[
            "Разработка REST API",
            "Работа с базами данных",
        ],
        is_python_related=True,
    )

    with open(
        "data/candidate_profile.json",
        encoding="utf-8",
    ) as file:
        candidate_profile = json.load(file)

    resume_text = """
    Python Developer.

    Навыки:
    Python, FastAPI, PostgreSQL, Git.

    Проекты:
    Разработка REST API на FastAPI.
    Работа с PostgreSQL.
    """

    service = CoverLetterService()

    result = service.generate_checked(
        vacancy=vacancy,
        candidate_profile=candidate_profile,
        resume_text=resume_text,
        max_attempts=2,
    )

    print("\nРЕЗУЛЬТАТ:")
    print("Успешная проверка:", result["success"])
    print("Попыток:", result["attempts"])

    print("\nСОПРОВОДИТЕЛЬНОЕ ПИСЬМО:")
    print(result["letter"])

    print("\nОЦЕНКА:")
    print(
        json.dumps(
            result["review"],
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
