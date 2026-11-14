from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.integrations.openrouter import get_llm


class SourceClassification(BaseModel):
    is_job_source: bool = Field(description="Публикуются ли в источнике вакансии")
    python_jobs_present: bool = Field(
        description="Встречаются ли вакансии Python-разработчиков",
    )
    it_jobs_present: bool = Field(
        description="Встречаются ли вакансии других IT-специалистов",
    )
    category: str = Field(
        description="Например: python_jobs, it_jobs, mixed, other",
    )
    confidence: int = Field(
        ge=0,
        le=100,
        description="Уверенность классификации",
    )
    job_post_frequency: str = Field(
        description="Частота вакансий: high, medium, low, unknown",
    )
    description: str = Field(description="Краткое объяснение классификации на русском")


class TelegramSourceClassifier:
    def __init__(self):
        self.llm = get_llm()
        self.structured_llm = self.llm.with_structured_output(
            SourceClassification,
            method="function_calling",
        )

    def classify(
        self,
        source_title: str,
        messages: list[str],
    ) -> SourceClassification:

        samples = "\n\n---\n\n".join(messages)

        result = self.structured_llm.invoke(
            [
                SystemMessage(
                    content="""
                Ты анализируешь Telegram-группы и каналы.

                Определи, публикуются ли в источнике вакансии.

                Учитывай:
                - реальные объявления о работе;
                - вакансии Python-разработчиков;
                - вакансии других IT-специалистов;
                - объявления о поиске сотрудников;
                - контакты рекрутеров и ссылки на отклик.

                Не считай вакансиями:
                - обсуждения поиска работы без объявлений;
                - рекламу курсов;
                - новости IT;
                - обучающие материалы;
                - обычное общение участников.

                Если сообщений недостаточно для уверенного вывода,
                снижай confidence.

                Не делай вывод только по названию группы.
                Основывайся прежде всего на содержании сообщений.
                """
                ),
                HumanMessage(
                    content=f"""
                Название источника:
                {source_title}

                Примеры последних сообщений:
                {samples}

                Классифицируй источник.
                """
                ),
            ]
        )

        return result
