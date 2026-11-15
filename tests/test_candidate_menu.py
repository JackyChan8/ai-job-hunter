from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agents import candidate_menu
from src.agents.candidate_menu import CandidateMenu, get_mode, set_mode
from src.core import settings
from src.services.candidate import (
    get_resume_status,
    load_candidate_profile,
    load_resume_text,
)

RESUME = (
    "Ivan Petrov. Python Developer with five years of experience. "
    "Built REST APIs with FastAPI and Django, PostgreSQL, Redis, Docker. "
    "Led a small team and wrote automated tests with pytest. "
    "Designed database schemas, optimized slow SQL queries, set up CI/CD "
    "pipelines and monitoring with Grafana and Prometheus."
)


class FakeMessage:
    def __init__(self, text=None, document=None):
        self.text = text
        self.document = document
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


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.message = FakeMessage()
        self.answer = AsyncMock()
        self.edits: list[tuple[str, object]] = []

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))

    @property
    def last_text(self):
        return self.edits[-1][0]

    @property
    def last_buttons(self):
        markup = self.edits[-1][1]
        return [button for row in markup.inline_keyboard for button in row]


def make_menu() -> CandidateMenu:
    return CandidateMenu(lambda update: update.effective_user.id == settings.TELEGRAM_ADMIN_USER_ID)


def ctx():
    return SimpleNamespace(user_data={})


async def press(menu, context, data, user_id=42):
    query = FakeQuery(data)
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=user_id),
        effective_message=query.message,
    )
    await menu.on_callback(update, context)
    return query


async def send_text(menu, context, text):
    message = FakeMessage(text=text)
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=42))
    await menu.handle_field_text(update, context, get_mode(context.user_data))
    return message


def make_document(pdf_bytes, name="cv.pdf", size=None):
    telegram_file = SimpleNamespace(
        download_as_bytearray=AsyncMock(return_value=bytearray(pdf_bytes))
    )
    return SimpleNamespace(
        file_name=name,
        file_size=size if size is not None else len(pdf_bytes),
        get_file=AsyncMock(return_value=telegram_file),
    )


async def upload(menu, context, document):
    message = FakeMessage(document=document)
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=42))
    await menu.handle_document(update, context)
    return message


# ------------------------------------------------------------------ профиль


@pytest.mark.asyncio
async def test_profile_command_lists_all_fields_as_buttons():
    menu = make_menu()
    message = FakeMessage()
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=42))

    await menu.profile_command(update, ctx())

    text, markup = message.sent[0]
    for label in ("Желаемые роли", "Опыт", "Навыки", "Формат работы", "Тип занятости",
                  "Минимальная зарплата", "Языки", "Локации", "Дополнительно"):
        assert label in text
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "p:e:skills" in callbacks and "p:resume" in callbacks
    assert all(len(data.encode()) <= 64 for data in callbacks)


@pytest.mark.asyncio
async def test_list_field_add_then_add_again_merges_without_duplicates():
    menu, context = make_menu(), ctx()

    query = await press(menu, context, "p:e:skills")
    assert "Что сделать со списком" in query.last_text

    await press(menu, context, "p:o:skills:add")
    assert get_mode(context.user_data)["op"] == "add"
    reply = await send_text(menu, context, "FastAPI, docker")
    assert reply.texts[0] == "✅ Сохранено: Навыки"
    assert get_mode(context.user_data) is None

    await press(menu, context, "p:o:skills:add")
    await send_text(menu, context, "Docker\nRedis")
    assert load_candidate_profile()["skills"] == ["FastAPI", "docker", "Redis"]


@pytest.mark.asyncio
async def test_list_field_replace():
    menu, context = make_menu(), ctx()
    await press(menu, context, "p:o:skills:add")
    await send_text(menu, context, "A, B, C")

    await press(menu, context, "p:o:skills:rep")
    await send_text(menu, context, "X")
    assert load_candidate_profile()["skills"] == ["X"]


@pytest.mark.asyncio
async def test_list_field_clear_button():
    menu, context = make_menu(), ctx()
    await press(menu, context, "p:o:target_roles:rep")
    await send_text(menu, context, "Python Developer")

    query = await press(menu, context, "p:c:target_roles")
    assert load_candidate_profile()["target_roles"] == []
    assert "ПРОФИЛЬ КАНДИДАТА" in query.last_text


@pytest.mark.asyncio
async def test_scalar_fields_experience_and_salary():
    menu, context = make_menu(), ctx()

    await press(menu, context, "p:e:experience_years")
    await send_text(menu, context, "5 лет")
    assert load_candidate_profile()["experience_years"] == 5

    await press(menu, context, "p:e:minimum_salary")
    await send_text(menu, context, "200к")
    assert load_candidate_profile()["minimum_salary"] == 200000

    await press(menu, context, "p:c:minimum_salary")
    assert load_candidate_profile()["minimum_salary"] is None


@pytest.mark.asyncio
async def test_invalid_input_keeps_mode_and_profile_unchanged():
    menu, context = make_menu(), ctx()
    await press(menu, context, "p:e:minimum_salary")

    reply = await send_text(menu, context, "договорная")

    assert reply.texts[0].startswith("⚠️")
    assert get_mode(context.user_data) is not None  # можно сразу ввести заново
    assert load_candidate_profile()["minimum_salary"] is None

    await send_text(menu, context, "100000")
    assert load_candidate_profile()["minimum_salary"] == 100000


@pytest.mark.asyncio
async def test_additional_information_text():
    menu, context = make_menu(), ctx()
    await press(menu, context, "p:e:additional_information")
    await send_text(menu, context, "Ищу продуктовую компанию.\nНе интересует аутсорс.")
    assert "аутсорс" in load_candidate_profile()["additional_information"]


@pytest.mark.asyncio
async def test_toggle_options_for_work_formats():
    menu, context = make_menu(), ctx()

    query = await press(menu, context, "p:e:preferred_work_formats")
    assert [b.text for b in query.last_buttons][:3] == ["⬜ Удалённо", "⬜ Гибрид", "⬜ Офис"]

    query = await press(menu, context, "p:t:preferred_work_formats:0")
    assert load_candidate_profile()["preferred_work_formats"] == ["Удалённо"]
    assert query.last_buttons[0].text == "✅ Удалённо"

    await press(menu, context, "p:t:preferred_work_formats:1")
    await press(menu, context, "p:t:preferred_work_formats:0")  # снять
    assert load_candidate_profile()["preferred_work_formats"] == ["Гибрид"]


@pytest.mark.asyncio
async def test_toggle_keeps_custom_values_and_ignores_bad_index():
    menu, context = make_menu(), ctx()
    from src.services.candidate import update_candidate_profile

    update_candidate_profile("preferred_employment_types", ["Контракт"])
    await press(menu, context, "p:t:preferred_employment_types:0")
    assert load_candidate_profile()["preferred_employment_types"] == ["Контракт", "Полная занятость"]

    await press(menu, context, "p:t:preferred_employment_types:99")  # не падает
    assert len(load_candidate_profile()["preferred_employment_types"]) == 2


@pytest.mark.asyncio
async def test_stranger_cannot_press_buttons():
    menu, context = make_menu(), ctx()
    query = await press(menu, context, "p:o:skills:rep", user_id=999)
    query.answer.assert_awaited_once_with("⛔ Нет доступа.", show_alert=True)
    assert get_mode(context.user_data) is None


@pytest.mark.asyncio
async def test_mode_expires(monkeypatch):
    context = ctx()
    now = [1000.0]
    monkeypatch.setattr(candidate_menu.time, "monotonic", lambda: now[0])

    set_mode(context.user_data, "vacancy")
    assert get_mode(context.user_data) is not None

    now[0] += candidate_menu.MODE_TTL_SECONDS + 1
    assert get_mode(context.user_data) is None


# ------------------------------------------------------------------ резюме


@pytest.mark.asyncio
async def test_resume_upload_flow(make_pdf):
    menu, context = make_menu(), ctx()

    message = FakeMessage()
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=42))
    await menu.resume_command(update, context)
    assert "ЗАГРУЗКА РЕЗЮМЕ" in message.texts[0]
    assert get_mode(context.user_data)["name"] == "resume"

    reply = await upload(menu, context, make_document(make_pdf(RESUME), name="ivan_cv.pdf"))

    final_text, markup = reply.status.edits[-1]
    assert "✅ Резюме загружено: ivan_cv.pdf" in final_text
    assert "Python Developer" in final_text  # превью текста
    assert markup.inline_keyboard[0][0].callback_data == "p:show"
    assert get_mode(context.user_data) is None
    assert get_resume_status()["filename"] == "ivan_cv.pdf"
    assert "FastAPI" in load_resume_text()


@pytest.mark.asyncio
async def test_resume_upload_outside_resume_mode_gives_hint(make_pdf):
    reply = await upload(make_menu(), ctx(), make_document(make_pdf(RESUME)))
    assert "/resume" in reply.texts[0]
    assert get_resume_status()["source"] == "none"


@pytest.mark.asyncio
async def test_bad_pdf_keeps_resume_mode_for_retry(make_pdf):
    menu, context = make_menu(), ctx()
    set_mode(context.user_data, "resume")

    reply = await upload(menu, context, make_document(b"not a pdf"))
    assert reply.status.edits[-1][0].startswith("⚠️ Не удалось прочитать")
    assert get_mode(context.user_data)["name"] == "resume"

    reply = await upload(menu, context, make_document(make_pdf("scan")))
    assert "почти нет текста" in reply.status.edits[-1][0]

    await upload(menu, context, make_document(make_pdf(RESUME)))
    assert get_resume_status()["source"] == "uploaded"


@pytest.mark.asyncio
async def test_too_large_file_is_rejected_before_download():
    menu, context = make_menu(), ctx()
    set_mode(context.user_data, "resume")
    document = make_document(b"x", size=11 * 1024 * 1024)

    reply = await upload(menu, context, document)

    assert "10 МБ" in reply.texts[0]
    document.get_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_resume_replaces_old(make_pdf):
    menu, context = make_menu(), ctx()

    for marker in ("FIRST", "SECOND"):
        set_mode(context.user_data, "resume")
        await upload(menu, context, make_document(make_pdf(RESUME + f" {marker}")))

    assert "SECOND" in load_resume_text() and "FIRST" not in load_resume_text()
