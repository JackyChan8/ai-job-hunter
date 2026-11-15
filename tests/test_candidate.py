import json

import pytest

from src.core import settings
from src.services import candidate
from src.services.candidate import (
    ResumeError,
    extract_resume_text_from_pdf,
    get_resume_status,
    load_candidate_profile,
    load_resume_text,
    save_resume,
    update_candidate_profile,
)

RESUME = (
    "Ivan Petrov. Python Developer with five years of experience. "
    "Built REST APIs with FastAPI and Django, PostgreSQL, Redis, Docker. "
    "Led a small team and wrote automated tests with pytest. "
    "Designed database schemas, optimized slow SQL queries, set up CI/CD "
    "pipelines and monitoring with Grafana and Prometheus."
)


def test_profile_is_empty_when_nothing_exists():
    profile = load_candidate_profile()
    assert profile["skills"] == []
    assert profile["minimum_salary"] is None


def test_profile_falls_back_to_file(tmp_path, monkeypatch):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"skills": ["Python"], "experience_years": 5}))
    monkeypatch.setattr(settings, "CANDIDATE_PROFILE_PATH", str(path))

    profile = load_candidate_profile()
    assert profile["skills"] == ["Python"]
    assert profile["experience_years"] == 5


def test_update_overrides_file_and_keeps_other_fields(tmp_path, monkeypatch):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"skills": ["Python"], "experience_years": 5}))
    monkeypatch.setattr(settings, "CANDIDATE_PROFILE_PATH", str(path))

    update_candidate_profile("minimum_salary", 200000)
    profile = load_candidate_profile()

    # Данные из файла стали основой профиля в БД, правка добавилась сверху.
    assert profile["skills"] == ["Python"]
    assert profile["experience_years"] == 5
    assert profile["minimum_salary"] == 200000

    # Правка файла после этого уже ни на что не влияет: источник — БД.
    path.write_text(json.dumps({"skills": ["Go"]}))
    assert load_candidate_profile()["skills"] == ["Python"]


def test_update_validates_values():
    with pytest.raises(ValueError):
        update_candidate_profile("experience_years", -1)
    with pytest.raises(ValueError):
        update_candidate_profile("no_such_field", 1)
    with pytest.raises(ValueError):
        update_candidate_profile("skills", "not-a-list")


def test_corrupted_stored_profile_falls_back_to_file(tmp_path, monkeypatch):
    from src.db import set_candidate_value

    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"skills": ["FromFile"]}))
    monkeypatch.setattr(settings, "CANDIDATE_PROFILE_PATH", str(path))
    set_candidate_value("profile", "{broken json")

    assert load_candidate_profile()["skills"] == ["FromFile"]


def test_extract_resume_text_from_pdf(make_pdf):
    text, pages = extract_resume_text_from_pdf(make_pdf(RESUME))
    assert pages == 1
    assert "Python Developer" in text and "pytest" in text


def test_scanned_or_short_pdf_is_rejected(make_pdf):
    with pytest.raises(ResumeError, match="почти нет текста"):
        extract_resume_text_from_pdf(make_pdf("Too short"))


def test_garbage_is_rejected():
    with pytest.raises(ResumeError, match="Не удалось прочитать"):
        extract_resume_text_from_pdf(b"this is not a pdf")


def test_resume_status_and_priority(make_pdf, tmp_path, monkeypatch):
    assert get_resume_status() == {"source": "none"}
    assert load_resume_text() == ""

    # Резюме-файл на сервере — запасной вариант.
    file_path = tmp_path / "resume.pdf"
    file_path.write_bytes(make_pdf(RESUME + " FROM FILE"))
    monkeypatch.setattr(settings, "RESUME_PATH", str(file_path))
    assert get_resume_status() == {"source": "file", "filename": "resume.pdf"}
    assert "FROM FILE" in load_resume_text()

    # Загруженное через бота резюме приоритетнее.
    save_resume("UPLOADED RESUME TEXT", "my_cv.pdf", pages=2)
    status = get_resume_status()
    assert status["source"] == "uploaded"
    assert status["filename"] == "my_cv.pdf"
    assert status["pages"] == 2
    assert load_resume_text() == "UPLOADED RESUME TEXT"


def test_resume_text_is_truncated(make_pdf, monkeypatch):
    monkeypatch.setattr(candidate, "MAX_RESUME_CHARS", 100)
    text, _ = extract_resume_text_from_pdf(make_pdf(RESUME))
    assert len(text) == 100


def test_matcher_and_pipeline_read_profile_live():
    from src.services.vacancy.matcher import VacancyMatcher

    matcher = VacancyMatcher.__new__(VacancyMatcher)
    matcher.profile_path = None

    assert matcher.profile.skills == []
    update_candidate_profile("skills", ["Rust"])
    assert matcher.profile.skills == ["Rust"]  # без пересоздания объекта
