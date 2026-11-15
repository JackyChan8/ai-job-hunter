import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.candidate_menu import CandidateMenu, get_mode, is_onboarding
from src.agents.source_manager import TelegramSourceManager
from src.core import settings
from src.integrations import telegram_listener
from src.services.candidate import (
    confirm_field,
    get_setup_status,
    save_resume,
    update_candidate_profile,
)
from src.services.vacancy.pipeline import VacancyPipeline

RESUME = (
    "Ivan Petrov. Python Developer with five years of experience. "
    "Built REST APIs with FastAPI and Django, PostgreSQL, Redis, Docker. "
    "Led a small team and wrote automated tests with pytest. "
    "Designed database schemas, optimized slow SQL queries, set up CI/CD "
    "pipelines and monitoring with Grafana and Prometheus."
)


# ------------------------------------------------------------------ статус


def test_fresh_install_is_not_ready_and_starts_with_resume():
    status = get_setup_status()
    assert not status.complete
    assert status.next_step == "resume"
    assert status.missing_labels[0] == "Резюме (PDF)"
    assert len(status.missing_labels) == 10  # резюме + 9 полей


def test_steps_follow_the_requested_field_order():
    save_resume("x" * 300, "cv.pdf", 1)
    expected = [
        "target_roles", "experience_years", "skills", "preferred_work_formats",
        "preferred_employment_types", "minimum_salary", "languages",
        "preferred_locations", "additional_information",
    ]
    assert list(get_setup_status().incomplete_fields) == expected
    assert get_setup_status().next_step == "target_roles"


def test_required_fields_cannot_be_empty_even_if_confirmed():
    update_candidate_profile("skills", ["Python"])
    update_candidate_profile("skills", [])  # очистили
    assert "skills" in get_setup_status().incomplete_fields


def test_optional_fields_can_be_explicitly_left_empty():
    update_candidate_profile("minimum_salary", None)
    update_candidate_profile("languages", [])
    update_candidate_profile("preferred_locations", [])
    update_candidate_profile("additional_information", "")
    incomplete = get_setup_status().incomplete_fields
    for key in ("minimum_salary", "languages", "preferred_locations", "additional_information"):
        assert key not in incomplete


def test_experience_zero_needs_an_explicit_answer():
    assert "experience_years" in get_setup_status().incomplete_fields
    update_candidate_profile("experience_years", 0)
    assert "experience_years" not in get_setup_status().incomplete_fields


def test_file_profile_does_not_count_until_confirmed(tmp_path, monkeypatch):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"skills": ["Python"], "target_roles": ["Dev"]}))
    monkeypatch.setattr(settings, "CANDIDATE_PROFILE_PATH", str(path))

    assert "skills" in get_setup_status().incomplete_fields  # значение есть, ответа нет

    confirm_field("skills")
    assert "skills" not in get_setup_status().incomplete_fields

    # Файл правят позже, а подтверждённое значение не меняется.
    path.write_text(json.dumps({"skills": ["Go"]}))
    from src.services.candidate import load_candidate_profile

    assert load_candidate_profile()["skills"] == ["Python"]


def test_resume_file_on_server_does_not_count(tmp_path, make_pdf, monkeypatch):
    path = tmp_path / "resume.pdf"
    path.write_bytes(make_pdf(RESUME))
    monkeypatch.setattr(settings, "RESUME_PATH", str(path))
    assert not get_setup_status().resume_ok


def test_complete_with_fixture(setup_done):
    assert get_setup_status().complete
    assert get_setup_status().next_step is None


# ------------------------------------------------------------------ пайплайн


def _pipeline():
    pipeline = VacancyPipeline.__new__(VacancyPipeline)
    pipeline.analyzer = SimpleNamespace(analyze=MagicMock(side_effect=RuntimeError("reached")))
    return pipeline


@pytest.mark.asyncio
async def test_pipeline_does_nothing_until_setup_is_complete():
    pipeline = _pipeline()
    await pipeline.process("Python Developer, FastAPI, Docker " * 10)
    pipeline.analyzer.analyze.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_runs_after_setup(setup_done):
    pipeline = _pipeline()
    with pytest.raises(RuntimeError, match="reached"):
        await pipeline.process("Python Developer, FastAPI, Docker " * 10)


# ------------------------------------------------------------------ Listener


@pytest.mark.asyncio
async def test_listener_waits_until_setup_complete(monkeypatch):
    listener = telegram_listener.TelegramVacancyListener.__new__(
        telegram_listener.TelegramVacancyListener
    )
    monkeypatch.setattr(listener, "SETUP_CHECK_INTERVAL", 0, raising=False)

    incomplete = SimpleNamespace(complete=False, missing_labels=["Резюме (PDF)"])
    complete = SimpleNamespace(complete=True, missing_labels=[])
    answers = iter([incomplete, incomplete, incomplete, complete])
    calls = []

    def fake_status():
        calls.append(1)
        return next(answers)

    monkeypatch.setattr(telegram_listener, "get_setup_status", fake_status)

    await listener.wait_for_setup()
    assert len(calls) == 4  # трижды ждал и запустился на четвёртой проверке


@pytest.mark.asyncio
async def test_listener_starts_immediately_when_ready(setup_done):
    listener = telegram_listener.TelegramVacancyListener.__new__(
        telegram_listener.TelegramVacancyListener
    )
    await listener.wait_for_setup()  # не зависает


# ------------------------------------------------------------------ бот


class FakeMessage:
    def __init__(self, text=None, document=None):
        self.text = text
        self.document = document
        self.caption = None
        self.photo = []
        self.media_group_id = None
        self.sent: list[tuple[str, object]] = []
        self.status = SimpleNamespace(edits=[])

        async def edit_text(text, reply_markup=None):
            self.status.edits.append((text, reply_markup))

        self.status.edit_text = edit_text

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.sent.append((text, reply_markup))
        return self.status

    @property
    def texts(self):
        return [text for text, _ in self.sent]


def update_for(message, user_id=42):
    return SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=user_id))


def ctx():
    return SimpleNamespace(user_data={})


def buttons(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row] if markup else []


@pytest.mark.asyncio
async def test_bot_blocks_vacancies_until_setup():
    service = SimpleNamespace(from_text=AsyncMock(), from_images=AsyncMock())
    bot = TelegramSourceManager()
    bot._service = service

    # Текст
    message = FakeMessage(text="Python Developer ...")
    await bot.handle_text(update_for(message), ctx())
    assert "🔒" in message.texts[0] and "Осталось" in message.texts[0]
    assert "p:setup" in buttons(message.sent[0][1])

    # Фото
    message = FakeMessage()
    message.photo = [SimpleNamespace(get_file=AsyncMock())]
    await bot.handle_image(update_for(message), ctx())
    assert "🔒" in message.texts[0]

    # /vacancy
    message = FakeMessage()
    context = ctx()
    await bot.vacancy_command(update_for(message), context)
    assert "🔒" in message.texts[0]
    assert get_mode(context.user_data) is None

    service.from_text.assert_not_awaited()
    service.from_images.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_start_runs_wizard_when_not_set_up():
    bot = TelegramSourceManager()
    message = FakeMessage()
    context = ctx()

    await bot.start(update_for(message), context)

    assert "🔒" in message.texts[0]
    assert "ШАГ" in message.texts[1].upper() and "1 из 10" in message.texts[1]
    assert get_mode(context.user_data)["name"] == "resume"
    assert is_onboarding(context.user_data)


@pytest.mark.asyncio
async def test_bot_start_is_normal_when_ready(setup_done):
    bot = TelegramSourceManager()
    message = FakeMessage()
    await bot.start(update_for(message), ctx())
    assert "AI Job Hunter запущен" in message.texts[0]


@pytest.mark.asyncio
async def test_cancel_leaves_wizard_and_points_back_to_setup():
    bot = TelegramSourceManager()
    context = ctx()
    await bot.menu.setup_command(update_for(FakeMessage()), context)
    assert is_onboarding(context.user_data)

    message = FakeMessage()
    await bot.cancel_command(update_for(message), context)
    assert "/setup" in message.texts[0]
    assert not is_onboarding(context.user_data)
    assert get_mode(context.user_data) is None


# ------------------------------------------------------------------ мастер: все 10 шагов


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.message = FakeMessage()
        self.answer = AsyncMock()
        self.edits: list[tuple[str, object]] = []

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


async def press(menu, context, data):
    query = FakeQuery(data)
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=42),
        effective_message=query.message,
    )
    await menu.on_callback(update, context)
    return query


async def send_text(menu, context, text):
    message = FakeMessage(text=text)
    await menu.handle_field_text(update_for(message), context, get_mode(context.user_data))
    return message


@pytest.mark.asyncio
async def test_full_wizard_walkthrough(make_pdf):
    menu = CandidateMenu(lambda u: u.effective_user.id == 42)
    context = ctx()

    # 1. резюме
    message = FakeMessage()
    await menu.setup_command(update_for(message), context)
    assert "1 из 10" in message.texts[0]

    telegram_file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(make_pdf(RESUME))))
    document = SimpleNamespace(
        file_name="cv.pdf", file_size=1000, get_file=AsyncMock(return_value=telegram_file)
    )
    upload = FakeMessage(document=document)
    await menu.handle_document(update_for(upload), context)
    assert "✅ Резюме загружено" in upload.status.edits[-1][0]
    # texts[0] — статус «Читаю резюме…», следующий шаг приходит сразу за ним.
    assert "2 из 10" in upload.texts[1] and "Желаемые роли" in upload.texts[1]
    assert not get_setup_status().complete

    # 2. роли (список, «оставить» недоступно: пока пусто)
    assert "p:keep:target_roles" not in buttons(upload.sent[1][1])
    assert "p:skip:target_roles" not in buttons(upload.sent[1][1])  # обязательное
    reply = await send_text(menu, context, "Python Developer, Backend Developer")
    assert "3 из 10" in reply.texts[1] and "Опыт" in reply.texts[1]

    # 3. опыт: «не важно» нет, нужно число
    assert "p:skip:experience_years" not in buttons(reply.sent[1][1])
    reply = await send_text(menu, context, "abc")
    assert reply.texts[0].startswith("⚠️")
    reply = await send_text(menu, context, "5")
    assert "4 из 10" in reply.texts[1] and "Навыки" in reply.texts[1]

    # 4. навыки
    reply = await send_text(menu, context, "Python, FastAPI, Docker")
    assert "5 из 10" in reply.texts[1]

    # 5. формат работы (кнопки): «Готово» без выбора не пускает дальше
    query = await press(menu, context, "p:next:preferred_work_formats")
    assert "Выбери хотя бы один вариант" in query.edits[-1][0]
    assert "5 из 10" in query.edits[-1][0]
    await press(menu, context, "p:t:preferred_work_formats:0")
    query = await press(menu, context, "p:next:preferred_work_formats")
    assert "6 из 10" in query.edits[-1][0]

    # 6. тип занятости
    await press(menu, context, "p:t:preferred_employment_types:0")
    query = await press(menu, context, "p:next:preferred_employment_types")
    assert "7 из 10" in query.edits[-1][0] and "Минимальная зарплата" in query.edits[-1][0]

    # 7-10. необязательные: можно пропустить
    assert "p:skip:minimum_salary" in buttons(query.edits[-1][1])
    query = await press(menu, context, "p:skip:minimum_salary")
    assert "8 из 10" in query.edits[-1][0]
    query = await send_text(menu, context, "Русский — родной, English — B2")
    assert "9 из 10" in query.texts[1]
    query = await press(menu, context, "p:skip:preferred_locations")
    assert "10 из 10" in query.edits[-1][0]
    assert not get_setup_status().complete  # остался последний шаг

    query = await send_text(menu, context, "Хочу в продуктовую компанию")

    assert "🎉 Настройка завершена" in query.texts[1]
    assert get_setup_status().complete
    assert not is_onboarding(context.user_data)
    assert get_mode(context.user_data) is None


@pytest.mark.asyncio
async def test_keep_as_is_confirms_prefilled_values(tmp_path, monkeypatch, make_pdf):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"target_roles": ["Python Developer"], "skills": ["Python"]}))
    monkeypatch.setattr(settings, "CANDIDATE_PROFILE_PATH", str(path))
    save_resume("x" * 300, "cv.pdf", 1)

    menu = CandidateMenu(lambda u: True)
    context = ctx()
    message = FakeMessage()
    await menu.setup_command(update_for(message), context)

    # Значение из файла показано и его можно оставить одним нажатием.
    assert "Python Developer" in message.texts[0]
    assert "p:keep:target_roles" in buttons(message.sent[0][1])

    query = await press(menu, context, "p:keep:target_roles")
    assert "Опыт" in query.edits[-1][0]
    assert "target_roles" not in get_setup_status().incomplete_fields


@pytest.mark.asyncio
async def test_keep_ignored_for_empty_value():
    save_resume("x" * 300, "cv.pdf", 1)
    menu = CandidateMenu(lambda u: True)
    context = ctx()
    await menu.setup_command(update_for(FakeMessage()), context)

    query = await press(menu, context, "p:keep:target_roles")  # значения нет
    assert "target_roles" in get_setup_status().incomplete_fields
    assert "Желаемые роли" in query.edits[-1][0]  # остались на том же шаге


@pytest.mark.asyncio
async def test_skip_ignored_for_required_field():
    save_resume("x" * 300, "cv.pdf", 1)
    menu = CandidateMenu(lambda u: True)
    context = ctx()
    await menu.setup_command(update_for(FakeMessage()), context)

    await press(menu, context, "p:skip:target_roles")
    assert "target_roles" in get_setup_status().incomplete_fields


@pytest.mark.asyncio
async def test_profile_screen_shows_lock_and_continue_button():
    menu = CandidateMenu(lambda u: True)
    message = FakeMessage()
    await menu.profile_command(update_for(message), ctx())

    text, markup = message.sent[0]
    assert "🔒 Настройка не завершена" in text
    assert "⚠️" in text
    assert "p:setup" in buttons(markup)


@pytest.mark.asyncio
async def test_profile_screen_when_ready(setup_done):
    menu = CandidateMenu(lambda u: True)
    message = FakeMessage()
    await menu.profile_command(update_for(message), ctx())
    text, markup = message.sent[0]
    assert "✅ Настройка завершена" in text and "⚠️" not in text
    assert "p:setup" not in buttons(markup)


@pytest.mark.asyncio
async def test_clearing_required_field_closes_the_gate_again(setup_done):
    assert get_setup_status().complete
    menu = CandidateMenu(lambda u: True)
    await press(menu, ctx(), "p:c:skills")
    assert not get_setup_status().complete
