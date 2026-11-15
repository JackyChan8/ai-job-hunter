import pytest

from src.services.profile_fields import (
    FIELDS_BY_KEY,
    PROFILE_FIELDS,
    ProfileInputError,
    format_value,
    merge_lists,
    parse_experience,
    parse_field_value,
    parse_list,
    parse_salary,
)


def test_all_requested_fields_are_present_in_order():
    assert [f.key for f in PROFILE_FIELDS] == [
        "target_roles",
        "experience_years",
        "skills",
        "preferred_work_formats",
        "preferred_employment_types",
        "minimum_salary",
        "languages",
        "preferred_locations",
        "additional_information",
    ]


def test_parse_list_handles_separators_bullets_and_duplicates():
    text = "Python, FastAPI; docker\n- PostgreSQL\n• Redis\n1. python\n\n"
    assert parse_list(text) == ["Python", "FastAPI", "docker", "PostgreSQL", "Redis"]


@pytest.mark.parametrize("text", ["", "  ,;\n ", "-"])
def test_parse_list_rejects_empty(text):
    with pytest.raises(ProfileInputError):
        parse_list(text)


def test_parse_list_limits():
    with pytest.raises(ProfileInputError, match="длинный"):
        parse_list("x" * 81)
    with pytest.raises(ProfileInputError, match="много"):
        parse_list(",".join(str(i) for i in range(101)))


def test_merge_lists_is_case_insensitive_and_keeps_order():
    assert merge_lists(["Python", "Docker"], ["docker", "Redis", "REDIS"]) == [
        "Python",
        "Docker",
        "Redis",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("5", 5), ("5 лет", 5), ("опыт: 12", 12), ("0", 0)],
)
def test_parse_experience(text, expected):
    assert parse_experience(text) == expected


@pytest.mark.parametrize("text", ["много", "", "99"])
def test_parse_experience_errors(text):
    with pytest.raises(ProfileInputError):
        parse_experience(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("150000", 150000),
        ("150 000", 150000),
        ("150к", 150000),
        ("150k", 150000),
        ("150 тыс", 150000),
        ("150,000", 150000),
    ],
)
def test_parse_salary(text, expected):
    assert parse_salary(text) == expected


@pytest.mark.parametrize("text", ["150000 руб", "$3000", "договорная", "-5"])
def test_parse_salary_rejects_currency_and_garbage(text):
    with pytest.raises(ProfileInputError):
        parse_salary(text)


def test_parse_text_limit():
    field = FIELDS_BY_KEY["additional_information"]
    assert parse_field_value(field, "  хочу в продукт  ") == "хочу в продукт"
    with pytest.raises(ProfileInputError):
        parse_field_value(field, "x" * 1001)


def test_format_value():
    skills = FIELDS_BY_KEY["skills"]
    assert format_value(skills, []) == "—"
    assert format_value(skills, ["a", "b"]) == "a, b"
    assert format_value(skills, [str(i) for i in range(20)]).endswith("… (+5)")
    assert format_value(FIELDS_BY_KEY["minimum_salary"], None) == "не указана"
    assert format_value(FIELDS_BY_KEY["minimum_salary"], 150000) == "150 000"
