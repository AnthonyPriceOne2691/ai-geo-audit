"""Скрипт выката, метки (Ч1.6а, A183): ни секретов, ни чужих имён, портов и адресов в выводе,
журнале хода и аргументах команд; файл окружения и журнал — под игнором. Сценарии растут со
срезами: каждая новая команда скрипта входит сюда."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import deploy_records as rec
import pytest
from deploy_fakes import (
    ROOT,
    Refused,
    changed,
    check_like,
    make_ctx,
    server_values,
    tool,
    write_env,
)


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Корни — во временном каталоге, исполнитель и проба — подставные (L38)."""
    return make_ctx(tmp_path, monkeypatch)


#: A183: метки — значения, которых в выводе, журнале и аргументах быть не должно.
MARKS = [f"mkq{n:02d}" for n in range(1, 16)] + ["4791234567", "198.51.100.208", "nb-alpha"]

MARKS += ["198.51.100.210", "nb-beta", *(str(port) for port in range(47911, 47917))]


def test_a183_no_mark_in_output_journal_or_arguments(ctx, capsys) -> None:
    """A183: метки в секретах файла окружения, мастер-ключе, входящем файле, пароле сайта, в
    домене, порте, подсети, адресах, в именах и портах соседа; ядро по удачным сценариям и с
    отказами — меток нет ни в выводе, ни в журнале `.deploy/`, ни в аргументах команд."""
    values = server_values(
        migrate="a1a1mkq01",
        app="b2b2mkq02",
        redis="c3c3mkq03",
        signing="d4d4mkq04",
        ALERT_TELEGRAM_BOT_TOKEN="1234567:mkq05",
        ALERT_TELEGRAM_CHAT_ID="-1004791234567",
        SITE_DOMAIN="mkq06.example.test",
        WEB_PORT="47911",
        FRONT_SUBNET="198.51.100.208/28",
        WEB_ADDRESS="198.51.100.210",
        NEIGHBOR_HEALTH_URLS="http://127.0.0.1:47912/mkq07,http://127.0.0.1:47913/mkq08",
    )
    ctx.paths.etc.joinpath("incoming").mkdir(parents=True)
    for name, mark in (
        ("master.key", "mkq11"),
        ("site-password", "mkq12"),
        ("incoming/x", "mkq13"),
    ):
        ctx.paths.etc.joinpath(name).write_text(mark, encoding="utf-8")
    machine = rec.server_machine("nb-alpha-mkq09", "nb-beta-mkq10", ("47914", "47915"))
    ctx.runner.become(machine + rec.ours())
    write_env(ctx, values)
    check_like(ctx)
    check_like(ctx, changed(machine, "nb-alpha-mkq09-web-1", restarts=3))
    for broken in (
        values | {"NEIGHBOR_HEALTH_URLS": "http://192.0.2.7:47916/mkq14"},
        values | {"SITE_DOMAIN": "*.mkq15.example.test"},
        values | {"JWT_SECRET": tool.PLACEHOLDER},
    ):
        write_env(ctx, broken)
        with pytest.raises(Refused):
            check_like(ctx)
    write_env(ctx, values, mode=0o640)
    with pytest.raises(Refused, match="шире 600"):
        check_like(ctx)
    write_env(ctx, values)
    ctx.probe = lambda url: int(url.rsplit("/", 1)[-1])
    with pytest.raises(ValueError, match="mkq07"):
        check_like(ctx)
    assert tool.main(["last"], ctx) == 0
    seen = ctx.out.getvalue() + "".join(capsys.readouterr())
    seen += "".join(p.read_text(encoding="utf-8") for p in ctx.paths.deploy_dir.iterdir())
    seen += "\n".join(" ".join(argv) for argv, _env in ctx.runner.calls)
    assert [mark for mark in MARKS if mark in seen] == []
    assert "итог: сбой скрипта — ValueError" in seen and "общих 35, перезапусков 1" in seen


def test_a183_server_tree_ignores_env_and_journal_secrets_live_outside() -> None:
    """A183: `git status --porcelain` в каталоге сервиса после выката пуст — файл окружения и
    `.deploy/` под игнором; мастер-ключ и входящие — вне дерева."""
    for rel in (".env", ".deploy/20261120T000000Z-check.log", ".deploy/lock"):
        argv = ["git", "-C", str(ROOT), "check-ignore", "--no-index", "-q", rel]
        assert subprocess.run(argv, capture_output=True, check=False).returncode == 0, rel
    assert not tool.Paths().etc.is_relative_to(tool.Paths().service)
