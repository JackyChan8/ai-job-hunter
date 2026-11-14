from src.integrations.openrouter import get_llm


def main():
    llm = get_llm()

    response = llm.invoke(
        "Ты AI-агент для поиска работы Python Developer. "
        "Представься и расскажи, какие задачи умеешь выполнять."
    )

    print(response.content)


if __name__ == "__main__":
    main()
