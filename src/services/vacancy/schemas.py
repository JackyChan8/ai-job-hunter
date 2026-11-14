from pydantic import BaseModel, Field


class VacancyData(BaseModel):
    """Структурированные данные о вакансии."""

    title: str | None = Field(default=None, description="Название вакансии")
    company: str | None = Field(default=None, description="Название компании")
    location: str | None = Field(default=None, description="Город или страна")
    work_format: str | None = Field(
        default=None,
        description="Формат работы: удалённо, офис или гибрид",
    )
    employment_type: str | None = Field(
        default=None,
        description="Тип занятости: полная, частичная, проектная и т.д.",
    )
    salary: str | None = Field(
        default=None,
        description="Зарплата и валюта, если указаны",
    )
    experience: str | None = Field(default=None, description="Требуемый опыт работы")
    requirements: list[str] = Field(
        default_factory=list,
        description="Требования к кандидату",
    )
    technologies: list[str] = Field(
        default_factory=list,
        description="Языки программирования, фреймворки и инструменты",
    )
    responsibilities: list[str] = Field(
        default_factory=list,
        description="Обязанности сотрудника",
    )
    contacts: list[str] = Field(
        default_factory=list,
        description="Email, Telegram и другие контакты для связи",
    )
    application_url: str | None = Field(default=None, description="Ссылка для отклика")
    summary: str | None = Field(
        default=None,
        description="Краткое описание вакансии на русском языке",
    )
    is_python_related: bool = Field(
        description="Связана ли вакансия с Python-разработкой",
    )
