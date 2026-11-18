"""Миграции: цепочка ревизий (A17)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_geo_audit.storage import models, revisions

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def write(directory: Path, name: str, number: str, down: str | None) -> None:
    down_text = f'"{down}"' if down else "None"
    body = f'revision: str = "{number}"\ndown_revision: str | None = {down_text}\n'
    (directory / name).write_text(body, encoding="utf-8")


def test_a17_chain_in_order_passes(tmp_path: Path) -> None:
    """A17: честная цепочка `0001` → `0002` проходит."""
    write(tmp_path, "0001_start.py", "0001", None)
    write(tmp_path, "0002_next.py", "0002", "0001")
    assert revisions.chain_problems(tmp_path) == []


def test_a17_two_revisions_with_one_number_are_named(tmp_path: Path) -> None:
    """A17: две ветки принесли по `0002` — красная с номером и именами обоих файлов."""
    write(tmp_path, "0001_start.py", "0001", None)
    write(tmp_path, "0002_a.py", "0002", "0001")
    write(tmp_path, "0002_b.py", "0002", "0001")
    assert revisions.chain_problems(tmp_path) == [
        "номер 0002 у нескольких ревизий: 0002_a.py, 0002_b.py"
    ]


def test_a17_a_skipped_number_is_named(tmp_path: Path) -> None:
    """A17: после `0001` идёт `0003` с `down_revision` `0001` — alembic молчит, проверка нет."""
    write(tmp_path, "0001_start.py", "0001", None)
    write(tmp_path, "0003_jump.py", "0003", "0001")
    assert revisions.chain_problems(tmp_path) == [
        "номер 0002 пропущен",
        "у 0003 down_revision 0001 вместо 0002",
    ]


def test_a17_no_revisions_refuse(tmp_path: Path) -> None:
    """A17: каталог без ревизий — отказ, а не «цепочка цела»."""
    with pytest.raises(models.NothingToCheck, match="ревизий не найдено"):
        revisions.chain_problems(tmp_path)


def test_a17_the_real_chain_is_whole() -> None:
    """A17: настоящий каталог ревизий проходит ту же проверку цепочки."""
    assert revisions.chain_problems(VERSIONS) == []
