from langchain_core.messages import HumanMessage, SystemMessage

from src.integrations.openrouter import get_llm
from src.services.vacancy.schemas import VacancyData


class CoverLetterGenerator:
    def __init__(self):
        self.llm = get_llm()

    def generate(
        self,
        vacancy: VacancyData,
        candidate_profile: dict,
        resume_text: str = "",
        feedback: dict | None = None,
    ) -> str:

        system_prompt = """
        Ты профессиональный карьерный консультант
        и эксперт по найму IT-специалистов.

        Твоя задача — написать персональное сопроводительное
        письмо, которое аргументированно объясняет,
        почему кандидат подходит на конкретную вакансию.

        Найди 2–4 наиболее сильных совпадения между
        требованиями работодателя и реальными навыками
        кандидата.

        Объясни потенциальную пользу кандидата для компании.

        Не выдумывай опыт, навыки, проекты и достижения.

        Не приписывай кандидату коммерческий опыт,
        если он указан только в учебных проектах.

        Пиши на русском языке.

        Стиль: профессиональный, уверенный, естественный.

        Длина: 150–250 слов.

        Верни только готовое сопроводительное письмо.
        """

        user_prompt = f"""
        ПРОФИЛЬ КАНДИДАТА:

        {candidate_profile}

        РЕЗЮМЕ:

        {resume_text}

        ВАКАНСИЯ:

        {vacancy.model_dump_json(indent=2)}

        ЗАМЕЧАНИЯ ПРОВЕРЯЮЩЕГО:

        {feedback or "Первичная генерация письма."}

        Используй замечания проверяющего для улучшения письма.

        Если обнаружены неподтверждённые факты:
        - Удали их.
        - Не пытайся заменить их другими выдуманными фактами.
        - Используй только информацию из резюме.

        Если не хватает аргументов:
        - Найди реальные преимущества кандидата.
        - Свяжи их с требованиями вакансии.

        Напиши персональное сопроводительное письмо.
        """

        response = self.llm.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
        )

        return response.content
