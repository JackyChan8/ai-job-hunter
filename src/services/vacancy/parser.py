from langchain_core.messages import HumanMessage, SystemMessage

from src.integrations.openrouter import get_llm
from src.services.vacancy.schemas import VacancyData


class VacancyAnalyzer:
    def __init__(self):
        self.llm = get_llm()

        self.structured_llm = self.llm.with_structured_output(
            VacancyData,
            method="function_calling",
        )

    def analyze(self, vacancy_text: str) -> VacancyData:
        if not vacancy_text.strip():
            raise ValueError("Текст вакансии не должен быть пустым")

        messages = [
            SystemMessage(
                content=(
                    "Ты HR-аналитик, специализирующийся на Python-вакансиях. "
                    "Извлекай информацию из текста вакансии и возвращай "
                    "структурированные данные. "
                    "Не придумывай отсутствующие сведения. "
                    "Если информация не указана, используй null "
                    "или пустой список. "
                    "Все описания и summary формулируй на русском языке. "
                    "Сохраняй названия технологий в оригинальном написании."
                )
            ),
            HumanMessage(
                content=f"Проанализируй эту вакансию:\n\n{vacancy_text}"
            ),
        ]

        result = self.structured_llm.invoke(messages)

        return result
