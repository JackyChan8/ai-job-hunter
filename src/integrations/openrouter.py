from langchain_openai import ChatOpenAI

from src.core import settings


def get_llm(model: str | None = None, temperature: float = 0.2):
    return ChatOpenAI(
        model=model or settings.OPENROUTER_MODEL,
        api_key=settings.OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        temperature=temperature,
    )
