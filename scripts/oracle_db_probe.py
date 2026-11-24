"""Проба прав роли базы изнутри контейнера процесса (A85, A170, A172).

Оракул подаёт файл на вход `python - <набор>` в контейнере api или разовом migrate: файл
самодостаточен, из `scripts/` ничего не импортирует (L87). Вход — имя набора команд; выход — одна
строка JSON: `current_user` и по каждой команде `ok` или SQLSTATE, без текста ошибки — в нём
бывают значения (L31). Вход не удался — `{"connect": SQLSTATE}`. Движок — сервиса
(`build_engine`), адрес — `DATABASE_URL` процесса. Код 0 — набор пройден, 2 — набор не назван.
"""

from __future__ import annotations

import asyncio
import json
import sys

from ai_geo_audit.storage.engine import build_engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

#: Наборы команд примеров: имя — (вид, команды). `each` — каждая команда своей транзакцией с
#: откатом; `together` — все одной транзакцией с откатом; `commit` — одной с фиксацией: так
#: создаются и удаляются таблицы проб.
SETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "login": ("each", ("SELECT 1",)),
    "service-denied": (
        "each",
        (
            "CREATE TABLE oracle_x (a int)",
            "CREATE SCHEMA oracle_s",
            "ALTER TABLE alembic_version ADD COLUMN x int",
            "TRUNCATE alembic_version",
            "DROP TABLE alembic_version",
        ),
    ),
    "migrate-create": ("together", ("CREATE TABLE oracle_x (a int)", "DROP TABLE oracle_x")),
    "probe-make": ("commit", ("CREATE TABLE oracle_probe (id bigserial PRIMARY KEY, note text)",)),
    "service-rights": (
        "each",
        (
            "INSERT INTO oracle_probe (note) VALUES ('oracle')",
            "SELECT count(*) FROM oracle_probe",
            "UPDATE oracle_probe SET note = 'probe'",
            "DELETE FROM oracle_probe",
            "TRUNCATE oracle_probe",
            "DROP TABLE oracle_probe",
        ),
    ),
    "probe-drop": ("commit", ("DROP TABLE oracle_probe",)),
    "legacy": (
        "each",
        ("SELECT count(*) FROM oracle_legacy", "INSERT INTO oracle_legacy DEFAULT VALUES"),
    ),
    "legacy-drop": ("commit", ("DROP TABLE oracle_legacy",)),
    "extra-make": ("commit", ("CREATE TABLE oracle_extra (a int)",)),
    "extra-truncate": ("each", ("TRUNCATE oracle_extra",)),
    "extra-drop": ("commit", ("DROP TABLE oracle_extra",)),
}


def sqlstate(exc: DBAPIError) -> str:
    """Код ошибки драйвера без её текста."""
    return str(getattr(exc.orig, "sqlstate", None) or "unknown")


async def attempt(conn: AsyncConnection, command: str) -> str:
    try:
        await conn.execute(text(command))
    except DBAPIError as exc:
        return sqlstate(exc)
    return "ok"


async def run_set(conn: AsyncConnection, kind: str, commands: tuple[str, ...]) -> dict[str, str]:
    """Команды набора по виду: `each` — откат после каждой; в конце — фиксация у `commit`,
    откат у остальных."""
    results: dict[str, str] = {}
    for command in commands:
        results[command] = await attempt(conn, command)
        if kind == "each":
            await conn.rollback()
    await (conn.commit() if kind == "commit" else conn.rollback())
    return results


async def probe(name: str, url: str | None = None) -> dict[str, object]:
    """Набор `name` адресом `url`, по умолчанию — адресом процесса: `current_user` и SQLSTATE по
    командам или `{"connect": SQLSTATE}`, если вход не удался."""
    kind, commands = SETS[name]
    engine = build_engine("oracle-probe", url=url)
    try:
        async with engine.connect() as conn:
            user = (await conn.execute(text("SELECT current_user"))).scalar()
            await conn.rollback()
            results = await run_set(conn, kind, commands)
    except DBAPIError as exc:
        return {"connect": sqlstate(exc)}
    finally:
        await engine.dispose()
    return {"current_user": user, "results": results}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in SETS:
        found: dict[str, object] = {"error": "набор пробы не назван или неизвестен"}
        code = 2
    else:
        found, code = asyncio.run(probe(argv[1])), 0
    sys.stdout.write(json.dumps(found, ensure_ascii=False) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
