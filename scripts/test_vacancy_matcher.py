from src.services.vacancy.matcher import VacancyMatcher
from src.services.vacancy.parser import VacancyAnalyzer


def main():
    vacancy_text = """
    Python Backend Developer

    Компания: Example Tech

    Требования:
    - Опыт Python от 2 лет
    - FastAPI
    - PostgreSQL
    - Docker
    - REST API

    Условия:
    - Удалённая работа
    - Зарплата от 150 000 рублей
    - Полная занятость
    """

    # Анализируем вакансию
    analyzer = VacancyAnalyzer()
    vacancy = analyzer.analyze(vacancy_text)

    # Оцениваем соответствие профилю
    matcher = VacancyMatcher()
    match = matcher.match(vacancy)

    print("\n===== ОЦЕНКА ВАКАНСИИ =====")
    print(match.model_dump_json(indent=2))

    print(f"\nОценка: {match.score}/100")
    print(f"Рекомендация: {match.recommendation}")


if __name__ == "__main__":
    main()
