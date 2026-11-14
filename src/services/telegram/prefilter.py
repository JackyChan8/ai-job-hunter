import re

KEYWORDS = [
    "python",
    "developer",
    "разработчик",
    "программист",
    "вакансия",
    "вакансии",
    "ищем сотрудника",
    "ищем разработчика",
    "ищем специалиста",
    "hiring",
    "job opening",
    "job offer",
    "remote job",
    "backend",
    "frontend",
    "software engineer",
    "работа",
    "зарплата",
    "опыт работы",
    "fastapi",
    "django",
]


def may_contain_jobs(text: str) -> bool:
    normalized = text.lower()

    return any(
        re.search(
            rf"(?<!\w){re.escape(keyword)}(?!\w)",
            normalized,
        )
        for keyword in KEYWORDS
    )
