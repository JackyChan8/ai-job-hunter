# Builder
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=2.3.2 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}" \
    && python -m venv /opt/venv

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /build

# Сначала копируем файлы зависимостей для Docker layer cache.
COPY pyproject.toml poetry.lock ./

# Устанавливаем только production-зависимости.
RUN poetry install --only main --no-root --no-ansi

# Runtime
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Создаём непривилегированного пользователя.
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --no-create-home app

WORKDIR /app

# Каталоги для SQLite, Telethon-сессии и логов.
RUN mkdir -p /app/db /app/credentials /app/logs \
    && chown -R app:app /app

# Переносим готовое виртуальное окружение из builder.
COPY --from=builder /opt/venv /opt/venv

# Копируем исходный код приложения.
COPY --chown=app:app src ./src
COPY --chown=app:app scripts ./scripts

USER app

CMD ["python", "-m", "scripts.run_telegram_listener"]
