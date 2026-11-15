import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.source_manager import TelegramSourceManager
from src.db import get_connection, init_db, is_notified
from src.services.vacancy.extractor import VacancyExtractionError
from src.services.vacancy.manual import ManualResult, ManualVacancyService
from src.services.vacancy.matcher import VacancyMatch
from src.services.vacancy.schemas import VacancyData

# Письма доступны только после настройки (резюме + профиль).
pytestmark = pytest.mark.usefixtures("setup_done")

VACANCY_TEXT = "Python Developer в Acme. Требуется FastAPI, PostgreSQL, Docker. " * 5


def make_vacancy(**overrides) -> VacancyData:
    data = {
        "title": "Python Developer",
        "company": "Acme",
        "requirements": ["FastAPI"],
        "technologies": ["Python"],
        "is_python_related": True,
    }
    data.update(overrides)
    return VacancyData(**data)


def make_match() -> VacancyMatch:
    return VacancyMatch(
        score=82,
        matched_skills=["Python", "FastAPI"],
        missing_skills=["Go"],
        explanation="ok",
        recommendation="apply",
    )


def make_service(vacancy=None, match=None, letter_result=None) -> ManualVacancyService:
    init_db()  # как в настоящем __init__
    service = ManualVacancyService.__new__(ManualVacancyService)
    service.analyzer = SimpleNamespace(analyze=lambda text: vacancy or make_vacancy())
    service.matcher = SimpleNamespace(match=lambda v: match or make_match())
    service.letter_calls = []

    def generate_checked(**kwargs):
        service.letter_calls.append(kwargs)
        return letter_result or {
            "success": True,
            "letter": "Здравствуйте! Меня заинтересовала вакансия.",
            "review": {"overall_score": 90, "factual_accuracy": 95},
        }

    service.cover_letter_service = SimpleNamespace(generate_checked=generate_checked)
    return service


# ---------------------------------------------------------------- service


@pytest.mark.asyncio
async def test_from_text_generates_letter_and_marks_notified():
    service = make_service()
    stages = []

    async def progress(text):
        stages.append(text)

    result = await service.from_text(VACANCY_TEXT, progress)

    assert result.letter.startswith("Здравствуйте")
    assert result.review_passed is True
    assert result.match.score == 82
    assert service.letter_calls[0]["language"] == "русском"
    assert any("Анализирую" in s for s in stages)
    assert any("письмо" in s for s in stages)

    with get_connection() as connection:
        row = connection.execute("SELECT fingerprint, source FROM vacancies").fetchone()
    assert row["source"] == "manual:text"
    assert is_notified(row["fingerprint"])  # Listener не пришлёт повторно


@pytest.mark.asyncio
async def test_from_text_with_link_fetches_page(monkeypatch):
    from src.services.vacancy import manual

    monkeypatch.setattr(manual, "fetch_vacancy_text", lambda url: f"Fetched from {url} " + VACANCY_TEXT)
    service = make_service()
    result = await service.from_text("https://example.com/job/7")
    assert result.letter
    with get_connection() as connection:
        sources = [r["source"] for r in connection.execute("SELECT source FROM vacancies")]
    assert "manual:link:https://example.com/job/7" in sources


@pytest.mark.asyncio
async def test_english_vacancy_gets_english_letter():
    service = make_service()
    await service.from_text("We are hiring a Python developer. FastAPI, Docker and PostgreSQL required. " * 3)
    assert service.letter_calls[0]["language"] == "английском"


@pytest.mark.asyncio
async def test_not_a_vacancy_is_rejected():
    service = make_service(vacancy=make_vacancy(title=None, requirements=[], responsibilities=[]))
    with pytest.raises(VacancyExtractionError, match="Не похоже"):
        await service.from_text("Привет, как дела? " * 20)
    assert service.letter_calls == []


@pytest.mark.asyncio
async def test_matcher_failure_does_not_block_letter():
    service = make_service()

    def broken(_):
        raise RuntimeError("LLM down")

    service.matcher = SimpleNamespace(match=broken)
    result = await service.from_text(VACANCY_TEXT + " уникальный хвост")
    assert result.match is None
    assert result.letter


# ---------------------------------------------------------------- bot


class FakeMessage:
    def __init__(self, text=None, caption=None, message_id=1, media_group_id=None, photo=None):
        self.text = text
        self.caption = caption
        self.message_id = message_id
        self.media_group_id = media_group_id
        self.photo = photo or []
        self.document = None
        self.replies: list[str] = []
        self.status = MagicMock()
        self.status.edit_text = AsyncMock(side_effect=lambda t: self.replies.append(f"[status] {t}"))
        self.chat = SimpleNamespace(send_action=AsyncMock())

    async def reply_text(self, text, **kwargs):
        if text.startswith("⏳"):
            self.replies.append(f"[status] {text}")
            return self.status
        self.replies.append(text)
        return self.status


def make_photo(data=b"img"):
    tg_file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(data)))
    return [SimpleNamespace(get_file=AsyncMock(return_value=tg_file))]


def make_bot(service) -> TelegramSourceManager:
    bot = TelegramSourceManager()
    bot._service = service
    return bot


def update_for(message, user_id=42):
    return SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=user_id),
    )


def ctx():
    return SimpleNamespace(user_data={})


@pytest.mark.asyncio
async def test_bot_text_flow_sends_summary_letter_and_review():
    result = ManualResult(
        vacancy=make_vacancy(salary="300 000 ₽"),
        match=make_match(),
        letter="Текст письма",
        review={"overall_score": 90, "factual_accuracy": 95},
        review_passed=True,
    )
    service = SimpleNamespace(from_text=AsyncMock(return_value=result))
    bot = make_bot(service)
    message = FakeMessage(text="https://example.com/job/1")

    await bot.handle_text(update_for(message), ctx())

    service.from_text.assert_awaited_once()
    assert service.from_text.await_args.args[0] == "https://example.com/job/1"
    joined = "\n".join(message.replies)
    assert "Python Developer" in joined and "82/100" in joined
    assert "Текст письма" in message.replies  # письмо — отдельным сообщением
    assert "Проверка пройдена ✅" in joined


@pytest.mark.asyncio
async def test_bot_shows_friendly_error_for_extraction_problem():
    service = SimpleNamespace(
        from_text=AsyncMock(side_effect=VacancyExtractionError("Страница пустая"))
    )
    message = FakeMessage(text="https://example.com/job/1")
    await make_bot(service).handle_text(update_for(message), ctx())
    assert message.replies[-1] == "[status] ⚠️ Страница пустая"


@pytest.mark.asyncio
async def test_bot_hides_internal_errors():
    service = SimpleNamespace(from_text=AsyncMock(side_effect=RuntimeError("secret key leaked")))
    message = FakeMessage(text="текст")
    await make_bot(service).handle_text(update_for(message), ctx())
    assert "secret" not in message.replies[-1]
    assert "Не удалось подготовить письмо" in message.replies[-1]


@pytest.mark.asyncio
async def test_bot_single_photo():
    result = ManualResult(vacancy=make_vacancy(), match=None, letter="Письмо")
    service = SimpleNamespace(from_images=AsyncMock(return_value=result))
    message = FakeMessage(caption="смотри", photo=make_photo(b"abc"))

    await make_bot(service).handle_image(update_for(message), ctx())

    images, caption, _ = service.from_images.await_args.args
    assert images == [(b"abc", "image/jpeg")]
    assert caption == "смотри"


@pytest.mark.asyncio
async def test_bot_album_is_processed_once_in_message_order(monkeypatch):
    from src.agents import source_manager

    monkeypatch.setattr(source_manager, "MEDIA_GROUP_WAIT_SECONDS", 0.05)
    result = ManualResult(vacancy=make_vacancy(), match=None, letter="Письмо")
    service = SimpleNamespace(from_images=AsyncMock(return_value=result))
    bot = make_bot(service)

    # Сообщения приходят вперемешку: id=11 раньше id=10.
    m2 = FakeMessage(message_id=11, media_group_id="g1", photo=make_photo(b"second"))
    m1 = FakeMessage(message_id=10, media_group_id="g1", caption="альбом", photo=make_photo(b"first"))
    m3 = FakeMessage(message_id=12, media_group_id="g1", photo=make_photo(b"third"))

    await asyncio.gather(
        bot.handle_image(update_for(m2), ctx()),
        bot.handle_image(update_for(m1), ctx()),
        bot.handle_image(update_for(m3), ctx()),
    )

    assert service.from_images.await_count == 1
    images, caption, _ = service.from_images.await_args.args
    assert [data for data, _ in images] == [b"first", b"second", b"third"]
    assert caption == "альбом"
    assert bot._media_groups == {}


@pytest.mark.asyncio
async def test_bot_album_limits_images(monkeypatch):
    from src.agents import source_manager

    monkeypatch.setattr(source_manager, "MEDIA_GROUP_WAIT_SECONDS", 0.05)
    result = ManualResult(vacancy=make_vacancy(), match=None, letter="Письмо")
    service = SimpleNamespace(from_images=AsyncMock(return_value=result))
    bot = make_bot(service)

    messages = [
        FakeMessage(message_id=i, media_group_id="big", photo=make_photo(bytes([i])))
        for i in range(1, 8)
    ]
    await asyncio.gather(*(bot.handle_image(update_for(m), ctx()) for m in messages))

    images, _, _ = service.from_images.await_args.args
    assert len(images) == 5


def test_handlers_ignore_strangers():
    from telegram import Chat, Message, Update, User
    from telegram.ext import MessageHandler

    bot = TelegramSourceManager()
    handlers = [
        h for h in bot.application.handlers[0] if isinstance(h, MessageHandler)
    ]
    assert len(handlers) == 3  # текст, картинки, PDF

    def update_from(user_id, chat_type="private"):
        user = User(id=user_id, first_name="x", is_bot=False)
        chat = Chat(id=user_id, type=chat_type)
        msg = Message(message_id=1, date=None, chat=chat, from_user=user, text="вакансия")
        return Update(update_id=1, message=msg)

    text_handler = handlers[0]
    assert text_handler.check_update(update_from(42)) is not None
    assert not text_handler.check_update(update_from(999))
    assert not text_handler.check_update(update_from(42, chat_type="group"))


# ---------------------------------------------------------------- режимы бота


@pytest.mark.asyncio
async def test_vacancy_command_waits_for_vacancy_and_is_one_shot():
    result = ManualResult(vacancy=make_vacancy(), match=None, letter="Письмо")
    service = SimpleNamespace(from_text=AsyncMock(return_value=result))
    bot = make_bot(service)
    context = ctx()

    command_message = FakeMessage()
    await bot.vacancy_command(update_for(command_message), context)
    assert "Пришли вакансию" in command_message.replies[0]
    assert context.user_data["mode"]["name"] == "vacancy"

    await bot.handle_text(update_for(FakeMessage(text="Python Developer ...")), context)
    service.from_text.assert_awaited_once()
    assert "mode" not in context.user_data  # режим сбросился


@pytest.mark.asyncio
async def test_vacancy_command_denied_for_strangers():
    bot = make_bot(SimpleNamespace())
    message = FakeMessage()
    context = ctx()
    await bot.vacancy_command(update_for(message, user_id=999), context)
    assert message.replies == ["⛔ Нет доступа."]
    assert "mode" not in context.user_data


@pytest.mark.asyncio
async def test_text_in_field_mode_is_not_treated_as_vacancy():
    service = SimpleNamespace(from_text=AsyncMock())
    bot = make_bot(service)
    context = ctx()

    from src.agents.candidate_menu import set_mode

    set_mode(context.user_data, "field", field="skills", op="rep")
    message = FakeMessage(text="Python, FastAPI")
    await bot.handle_text(update_for(message), context)

    service.from_text.assert_not_awaited()
    from src.services.candidate import load_candidate_profile

    assert load_candidate_profile()["skills"] == ["Python", "FastAPI"]


@pytest.mark.asyncio
async def test_text_in_resume_mode_asks_for_pdf():
    service = SimpleNamespace(from_text=AsyncMock())
    bot = make_bot(service)
    context = ctx()

    from src.agents.candidate_menu import set_mode

    set_mode(context.user_data, "resume")
    message = FakeMessage(text="вот моё резюме")
    await bot.handle_text(update_for(message), context)

    service.from_text.assert_not_awaited()
    assert "PDF" in message.replies[0]


@pytest.mark.asyncio
async def test_cancel_resets_mode():
    bot = make_bot(SimpleNamespace())
    context = ctx()

    from src.agents.candidate_menu import set_mode

    set_mode(context.user_data, "vacancy")
    message = FakeMessage()
    await bot.cancel_command(update_for(message), context)
    assert message.replies == ["Отменено."]

    message = FakeMessage()
    await bot.cancel_command(update_for(message), context)
    assert message.replies == ["Отменять нечего."]
