"""Скрипт выката, метки (Ч1.6а, A183): ни секретов, ни чужих имён, портов и адресов в выводе,
журнале хода и аргументах команд; файл окружения и журнал — под игнором. Сценарии растут со
срезами: каждая новая команда скрипта входит сюда."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import deploy_records as rec
import pytest
from deploy_fakes import (
    ROOT,
    changed,
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

MARKS += ["198.51.100.210", "nb-beta", *(str(port) for port in range(47911, 47918))]

#: Подсети соседа: сети докера, маршруты и строка `subnet` его файла compose (6а-3).
MARKS += ["10.47.0.0", "10.48.0.0", "10.49.0.0"]


def test_a183_no_mark_in_output_journal_or_arguments(ctx, capsys) -> None:
    """A183: метки в секретах файла окружения, мастер-ключе, входящем файле, пароле сайта, в
    домене, порте, подсети, адресах, в именах, портах и подсетях соседа — живых и в его файлах
    compose; `deploy.py check` через `main` по удачным сценариям, с отказами и с чужой ошибкой —
    меток нет ни в выводе, ни в журнале `.deploy/`, ни в аргументах команд; у чужой ошибки —
    только её вид."""
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
    alpha, beta, nets = "nb-alpha-mkq09", "nb-beta-mkq10", ("10.47.0.0/16", "10.48.0.0/16")
    machine = rec.server_machine(alpha, beta, ("47914", "47915"))
    ctx.runner.become(machine + rec.ours())
    ctx.runner.networks = rec.networks(alpha, beta, nets)
    ctx.runner.volumes = [rec.volume_line(f"{alpha}_ai-geo-audit-cache", alpha)]
    subnet = f"/srv/{beta}/docker-compose.yml:         - subnet: 10.49.0.0/16\n"
    ctx.runner.answers |= {
        "ss": (0, rec.listening(("47914", "47915"))),
        "find": (0, rec.compose_lines(alpha, beta, ("47916", "47917")) + subnet),
        "ip": (0, rec.routes(nets)),
    }
    write_env(ctx, values)
    assert tool.main(["check"], ctx) == 0
    # Сосед перезапустился между снимками одного запуска — снимок красный: отказ, без имён.
    ctx.runner.then = changed(machine, f"{alpha}-web-1", restarts=3) + rec.ours()
    assert tool.main(["check"], ctx) == 1
    # Порт и подсеть соседа — отказы со своими причинами, без значений.
    write_env(ctx, values | {"WEB_PORT": "47914", "FRONT_SUBNET": "10.48.0.0/28"})
    assert tool.main(["check"], ctx) == 1
    for broken in (
        values | {"NEIGHBOR_HEALTH_URLS": "http://192.0.2.7:47916/mkq14"},
        values | {"SITE_DOMAIN": "*.mkq15.example.test"},
        values | {"JWT_SECRET": tool.PLACEHOLDER},
    ):
        write_env(ctx, broken)
        assert tool.main(["check"], ctx) == 1
    write_env(ctx, values, mode=0o640)
    assert tool.main(["check"], ctx) == 1
    write_env(ctx, values)
    ctx.probe = lambda url: int(url.rsplit("/", 1)[-1])
    assert tool.main(["check"], ctx) == 1
    assert tool.main(["last"], ctx) == 0
    seen = ctx.out.getvalue() + "".join(capsys.readouterr())
    seen += "".join(p.read_text(encoding="utf-8") for p in ctx.paths.deploy_dir.iterdir())
    seen += "\n".join(" ".join(argv) for argv, _env in ctx.runner.calls)
    assert [mark for mark in MARKS if mark in seen] == []
    assert "итог: сбой скрипта — ValueError" in seen and "сбой скрипта: ValueError" in seen
    assert "общих 35, перезапусков 1" in seen and "шире 600" in seen


def test_a183_env_init_and_put_secret_print_no_values(ctx, capsys) -> None:
    """A183, растёт на env-init и put-secret (6а-2): значения, что создал скрипт, мастер-ключ и
    метки входящих файлов — ни в выводе, ни в журнале хода, и при отказе put-secret тоже; внешних
    команд нет."""
    ctx.key_owner = os.getuid()
    ctx.paths.etc.parent.mkdir()
    ctx.paths.service.joinpath(".env.example").write_bytes((ROOT / ".env.example").read_bytes())
    assert tool.main(["env-init"], ctx) == 0
    made = [line.split("=", 1)[1] for line in ctx.paths.env_file.read_text("utf-8").splitlines()]
    secrets = [value for value in made if len(value) >= 12 and value != tool.PLACEHOLDER]
    key = ctx.paths.etc.joinpath("master.key").read_text(encoding="ascii").strip()
    for name, text, code in (
        (
            "owner.env",
            "ALERT_TELEGRAM_BOT_TOKEN=1234567:mkq16\nSITE_DOMAIN=mkq17.example.test\n",
            0,
        ),
        ("foreign.env", "JWT_SECRET=mkq18\n", 1),  # pragma: allowlist secret
    ):
        path = ctx.paths.etc / "incoming" / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)
        assert tool.main(["put-secret", str(path)], ctx) == code
    assert tool.main(["last"], ctx) == 0
    seen = ctx.out.getvalue() + "".join(capsys.readouterr())
    seen += "".join(p.read_text(encoding="utf-8") for p in ctx.paths.deploy_dir.iterdir())
    assert [value for value in [*secrets, key, "mkq16", "mkq17", "mkq18"] if value in seen] == []
    assert ctx.runner.calls == []


def test_a183_server_tree_ignores_env_and_journal_secrets_live_outside() -> None:
    """A183: `git status --porcelain` в каталоге сервиса после выката пуст — файл окружения и
    `.deploy/` под игнором; мастер-ключ и входящие — вне дерева."""
    for rel in (".env", ".deploy/20261120T000000Z-check.log", ".deploy/lock"):
        argv = ["git", "-C", str(ROOT), "check-ignore", "--no-index", "-q", rel]
        assert subprocess.run(argv, capture_output=True, check=False).returncode == 0, rel
    assert not tool.Paths().etc.is_relative_to(tool.Paths().service)
