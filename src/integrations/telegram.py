from telegram import Bot

from src.core import settings
from src.services.vacancy.matcher import VacancyMatch
from src.services.vacancy.schemas import VacancyData

TELEGRAM_MESSAGE_LIMIT = 4096
SAFE_MESSAGE_LENGTH = 3900


def format_vacancy_message(
    vacancy: VacancyData,
    match: VacancyMatch,
    cover_letter: str,
    review: dict,
) -> str:
    skills = ", ".join(vacancy.technologies) or "Не указаны"
    contacts = "\n".join(vacancy.contacts) or "Не указаны"
    matched = ", ".join(match.matched_skills) or "Не выявлены"
    missing = ", ".join(match.missing_skills) or "Не выявлены"

    review_score = review.get("overall_score", "нет оценки")
    factual_score = review.get("factual_accuracy", "нет оценки")
    review_ok = bool(review.get("approved", False))

    review_status = (
        "✅ Проверка пройдена"
        if review_ok
        else "⚠️ Проверка не пройдена полностью — проверь письмо вручную"
    )

    unsupported = review.get("unsupported_claims") or []
    unsupported_text = (
        "\n".join(f"• {item}" for item in unsupported)
        if unsupported
        else "Не обнаружены"
    )

    letter_text = cover_letter.strip() or "Не удалось сгенерировать письмо."

    return f"""
🚀 НОВАЯ ВАКАНСИЯ

💼 Должность: {vacancy.title or "Не указана"}
🏢 Компания: {vacancy.company or "Не указана"}
💰 Зарплата: {vacancy.salary or "Не указана"}
📍 Локация: {vacancy.location or "Не указана"}
🏠 Формат: {vacancy.work_format or "Не указан"}
🕒 Занятость: {vacancy.employment_type or "Не указана"}
📈 Опыт: {vacancy.experience or "Не указан"}

⭐ СОВПАДЕНИЕ: {match.score}/100
📌 Рекомендация: {match.recommendation}

✅ Совпадающие навыки:
{matched}

⚠️ Навыки/требования для проверки:
{missing}

🛠 Технологии:
{skills}

📝 Описание:
{vacancy.summary or "Нет описания"}

📋 Обязанности:
{chr(10).join("• " + x for x in vacancy.responsibilities) or "Не указаны"}

📞 Контакты:
{contacts}

🔗 Ссылка на вакансию:
{vacancy.application_url or "Не указана"}

💡 Почему подходит:
{match.explanation}

━━━━━━━━━━━━━━━━━━
✉️ СОПРОВОДИТЕЛЬНОЕ ПИСЬМО

{letter_text}

━━━━━━━━━━━━━━━━━━
🔍 ПРОВЕРКА ПИСЬМА

{review_status}
Общая оценка: {review_score}/100
Достоверность: {factual_score}/100

Неподтверждённые утверждения:
{unsupported_text}

Письмо подготовлено AI. Перед использованием проверь его самостоятельно.
""".strip()


def format_manual_summary(
    vacancy: VacancyData,
    match: VacancyMatch | None,
) -> str:
    lines = [
        "✅ Вакансия разобрана",
        "",
        f"💼 {vacancy.title or 'Должность не указана'}",
        f"🏢 {vacancy.company or 'Компания не указана'}",
    ]

    if vacancy.salary:
        lines.append(f"💰 {vacancy.salary}")

    place = " · ".join(filter(None, (vacancy.location, vacancy.work_format)))
    if place:
        lines.append(f"📍 {place}")

    if match is not None:
        lines.append("")
        lines.append(f"⭐ Совпадение: {match.score}/100 ({match.recommendation})")

        if match.matched_skills:
            lines.append(f"✅ Совпадает: {', '.join(match.matched_skills[:8])}")
        if match.missing_skills:
            lines.append(f"⚠️ Не хватает: {', '.join(match.missing_skills[:8])}")

    return "\n".join(lines)


def format_manual_review_note(review: dict, review_passed: bool) -> str:
    if not review:
        return "🔍 Проверку письма выполнить не удалось — прочитай его внимательно."

    score = review.get("overall_score", "нет оценки")
    factual = review.get("factual_accuracy", "нет оценки")

    if review_passed:
        return (
            f"🔍 Проверка пройдена ✅ (оценка {score}/100, достоверность {factual}/100)\n"
            "Письмо подготовлено AI — перед отправкой перечитай его."
        )

    lines = [
        f"🔍 Проверка пройдена не полностью ⚠️ (оценка {score}/100, достоверность {factual}/100)"
    ]

    unsupported = review.get("unsupported_claims") or []
    if unsupported:
        lines.append("")
        lines.append("Не подтверждено резюме:")
        lines.extend(f"• {item}" for item in unsupported)

    lines.append("")
    lines.append("Проверь письмо вручную и убери лишнее.")
    return "\n".join(lines)


def split_telegram_text(text: str, limit: int = SAFE_MESSAGE_LENGTH) -> list[str]:
    """Разбивает длинный текст по границам строк, не теряя содержимое."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text

    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = limit

        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    if remaining:
        chunks.append(remaining)

    return chunks


async def send_vacancy(
    vacancy: VacancyData,
    match: VacancyMatch,
    cover_letter: str,
    review: dict,
) -> None:
    token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    chat_id = settings.TELEGRAM_CHAT_ID

    if not token:
        raise ValueError("Не задан TELEGRAM_BOT_TOKEN")
    if not chat_id:
        raise ValueError("Не задан TELEGRAM_CHAT_ID")

    message = format_vacancy_message(
        vacancy=vacancy,
        match=match,
        cover_letter=cover_letter,
        review=review,
    )

    chunks = split_telegram_text(message)

    async with Bot(token=token) as bot:
        for index, chunk in enumerate(chunks, start=1):
            prefix = f"📄 Часть {index}/{len(chunks)}\n\n" if len(chunks) > 1 else ""
            await bot.send_message(
                chat_id=chat_id,
                text=prefix + chunk,
                disable_web_page_preview=True,
            )
