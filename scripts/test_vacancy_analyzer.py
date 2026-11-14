from src.services.vacancy.parser import VacancyAnalyzer


def main():
    vacancy_text = """
    Python Developer

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

    analyzer = VacancyAnalyzer()
    result = analyzer.analyze(vacancy_text)

    print("\n===== РЕЗУЛЬТАТ АНАЛИЗА =====\n")
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
