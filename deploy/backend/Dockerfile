# Образ сервиса: api и migrate — один образ, разные команды; воркер — тот же образ (Часть 3).
# Контекст — корень репозитория, белый список — Dockerfile.dockerignore рядом (нужен BuildKit).
# Базы — тег линии и дайджест: последний патч линии старше 14 суток от выхода версии
# (decisions Ч1.5а, срез 0). uv 0.11.26 — та же версия, что на машине и в CI.

FROM ghcr.io/astral-sh/uv:0.11.26@sha256:3d868e555f8f1dbc324afa005066cd11e1053fc4743b9808ca8025283e65efa5 AS uv

# Установка: uv только на время сборки; сначала зависимости по uv.lock, потом проект без
# редактируемой установки. Python — из базы, uv его не скачивает.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS install
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /build
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --locked --no-dev --no-install-project --no-editable
COPY backend/src ./src
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --locked --no-dev --no-editable

# Итог: окружение, alembic.ini и migrations/; процессы — от uid 10001, в USER — числом.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS backend
# zlib1g и libzstd1 — исправления CVE-2026-85091 и CVE-2026-124851 из Debian (deb13u1). База
# пересобрана 2026-11-24 (191083cd…), но ей меньше 14 суток, и libzstd1 в ней прежний (decisions
# «Оракула» 2026-11-23 и Ч1.12а 2026-11-25). Строка уходит со сменой дайджеста базы.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1 libzstd1=1.5.7+dfsg-1+deb13u1 \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin app
COPY --from=install /opt/venv /opt/venv
WORKDIR /app
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 10001
CMD ["python", "-m", "ai_geo_audit.api"]
