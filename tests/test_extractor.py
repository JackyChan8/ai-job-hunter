import json

import pytest

from src.services.vacancy import extractor
from src.services.vacancy.extractor import (
    VacancyExtractionError,
    fetch_vacancy_text,
    find_urls,
    html_to_text,
    is_link_request,
    parse_html,
)
from src.services.vacancy.manual import detect_letter_language

LONG_TEXT = "Ищем Python-разработчика для работы над backend. " * 10


def test_find_urls_strips_trailing_punctuation():
    text = "Смотри https://example.com/job/1, и (https://x.org/a)."
    assert find_urls(text) == ["https://example.com/job/1", "https://x.org/a"]


def test_is_link_request():
    assert is_link_request("https://example.com/job/1")
    assert is_link_request("глянь вот это https://example.com/job/1")
    assert not is_link_request("Нет ссылок, просто текст вакансии")
    # Длинный текст со ссылкой на отклик — это текст вакансии, а не ссылка.
    assert not is_link_request(LONG_TEXT + " Откликнуться: https://example.com/apply")


def test_html_to_text_skips_scripts_and_keeps_structure():
    html = (
        "<html><head><style>.a{}</style><script>var x=1;</script></head>"
        "<body><h1>Python Dev</h1><ul><li>FastAPI</li><li>Docker</li></ul></body></html>"
    )
    text = html_to_text(html)
    assert "var x" not in text and ".a{}" not in text
    assert text.splitlines() == ["Python Dev", "FastAPI", "Docker"]


def test_parse_html_prefers_json_ld_job_posting():
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Backend Python Developer",
        "hiringOrganization": {"@type": "Organization", "name": "Acme"},
        "jobLocationType": "TELECOMMUTE",
        "description": "<p>Требования:</p><ul><li>Python</li><li>PostgreSQL</li></ul>" + LONG_TEXT,
    }
    html = (
        "<html><head><script type='application/ld+json'>"
        + json.dumps({"@graph": [posting]})
        + "</script></head><body>Cookie banner</body></html>"
    )
    text = parse_html(html)
    assert "Должность: Backend Python Developer" in text
    assert "Компания: Acme" in text
    assert "Формат: удалённо" in text
    assert "PostgreSQL" in text
    assert "Cookie banner" not in text


def test_parse_html_falls_back_to_page_text_on_broken_json_ld():
    html = (
        "<html><head><title>Вакансия</title>"
        "<script type='application/ld+json'>{broken</script></head>"
        f"<body><p>{LONG_TEXT}</p></body></html>"
    )
    text = parse_html(html)
    assert text.startswith("Вакансия")
    assert "Python-разработчика" in text


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/admin", "http://localhost:8080/", "http://169.254.169.254/latest/meta-data"],
)
def test_internal_addresses_are_blocked(url):
    with pytest.raises(VacancyExtractionError, match="внутренн"):
        fetch_vacancy_text(url)


def test_non_http_scheme_is_rejected():
    with pytest.raises(VacancyExtractionError):
        extractor._download("file:///etc/passwd")


def test_fetch_vacancy_text_too_short_page(monkeypatch):
    monkeypatch.setattr(
        extractor, "_download", lambda url, headers=None: ("<html><body>Loading…</body></html>", url)
    )
    with pytest.raises(VacancyExtractionError, match="почти нет текста"):
        fetch_vacancy_text("https://example.com/job")


def test_fetch_vacancy_text_ok_adds_source_link(monkeypatch):
    html = f"<html><body><p>{LONG_TEXT}</p></body></html>"
    monkeypatch.setattr(extractor, "_download", lambda url, headers=None: (html, url))
    text = fetch_vacancy_text("https://example.com/job")
    assert text.startswith("Ссылка на вакансию: https://example.com/job")
    assert "Python-разработчика" in text


def test_hh_vacancy_uses_api(monkeypatch):
    api_payload = {
        "name": "Python Developer",
        "employer": {"name": "HH Corp"},
        "area": {"name": "Москва"},
        "schedule": {"name": "Удалённая работа"},
        "salary": {"from": 200000, "to": None, "currency": "RUR"},
        "key_skills": [{"name": "Python"}, {"name": "FastAPI"}],
        "description": f"<p>{LONG_TEXT}</p>",
        "alternate_url": "https://hh.ru/vacancy/123",
    }
    calls = []

    def fake_download(url, headers=None):
        calls.append(url)
        return json.dumps(api_payload), url

    monkeypatch.setattr(extractor, "_download", fake_download)
    text = fetch_vacancy_text("https://hh.ru/vacancy/123?query=python")

    assert calls == ["https://api.hh.ru/vacancies/123"]
    assert "Компания: HH Corp" in text
    assert "от 200000 RUR" in text
    assert "Ключевые навыки: Python, FastAPI" in text


def test_hh_api_failure_falls_back_to_page(monkeypatch):
    html = f"<html><body><p>{LONG_TEXT}</p></body></html>"

    def fake_download(url, headers=None):
        if "api.hh.ru" in url:
            raise VacancyExtractionError("blocked")
        return html, url

    monkeypatch.setattr(extractor, "_download", fake_download)
    text = fetch_vacancy_text("https://hh.ru/vacancy/123")
    assert "Python-разработчика" in text


def test_extract_images_no_text(monkeypatch):
    class FakeResponse:
        content = "NO_TEXT"

    class FakeLLM:
        def invoke(self, messages):
            # Изображение должно уйти в модель как data-URL.
            human = messages[1].content
            assert human[1]["image_url"]["url"].startswith("data:image/png;base64,")
            return FakeResponse()

    monkeypatch.setattr(extractor, "get_llm", lambda **kwargs: FakeLLM())
    with pytest.raises(VacancyExtractionError, match="(?i)не получилось найти"):
        extractor.extract_text_from_images([(b"\x89PNG", "image/png")])


def test_extract_images_ok_with_caption(monkeypatch):
    class FakeResponse:
        content = LONG_TEXT

    class FakeLLM:
        def invoke(self, messages):
            return FakeResponse()

    monkeypatch.setattr(extractor, "get_llm", lambda **kwargs: FakeLLM())
    text = extractor.extract_text_from_images([(b"x", "image/jpeg")], caption="срочно")
    assert text.endswith("Подпись к изображению: срочно")


def test_detect_letter_language():
    assert detect_letter_language("Ищем Python разработчика") == "русском"
    assert detect_letter_language("We are looking for a Python developer") == "английском"
    assert detect_letter_language("12345 !!!") == "русском"
