"""Исполнитель команд Docker оракула без Docker (A172, вторая строка; A67): сырой захват вывода
каждой команды до маски, маска выдуманных секретов в причинах отказа и в хвосте журнала сервиса,
секреты прогона — в маске с созданием прогона. Модули `scripts/` лежат вне пакета."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import dbtools
import pytest

sys.path.insert(0, str(dbtools.ROOT / "scripts"))
docker_mod = importlib.import_module("stack_oracle_docker")
compose_mod = importlib.import_module("stack_oracle_compose")
oracle = importlib.import_module("stack_oracle")
INVENTED = "invented-value-123456"
ROLE = "m1x@pass-word-123456"


def answer(code: int = 0, out: bytes = b"", err: bytes = b"") -> Any:
    """Подставной `subprocess.run`: команда Docker отвечает кодом, stdout и stderr."""

    def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, code, out, err)

    return run


def test_a172_every_command_output_is_captured_raw_before_any_mask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A172, вторая строка: вывод каждой команды — stdout и stderr — ложится в сырой захват как
    есть, до маски; ответ команды — stdout как есть: шаги разбирают его сами."""
    out = f"out {INVENTED}\n".encode()
    monkeypatch.setattr(docker_mod.subprocess, "run", answer(0, out, b"err\n"))
    docker = docker_mod.Docker(mask=[INVENTED])
    assert docker("ps", "-a") == f"out {INVENTED}\n"
    assert docker("ps", "-q") == f"out {INVENTED}\n"
    assert docker.raw == [
        (("ps", "-a"), f"out {INVENTED}\nerr\n"),
        (("ps", "-q"), f"out {INVENTED}\nerr\n"),
    ]


def test_a172_failed_command_reason_hides_invented_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A172, вторая строка (A67): отказ команды — причина с хвостом stderr, где выдуманный секрет и
    пароль роли в процентной записи — звёздочками; в сыром захвате — как есть."""
    err = f"url=postgresql://audit_migrate:{quote(ROLE, safe='')}@db\nsecret {INVENTED}\n".encode()
    monkeypatch.setattr(docker_mod.subprocess, "run", answer(1, b"", err))
    docker = docker_mod.Docker(mask=[INVENTED, ROLE, quote(ROLE, safe="")])
    with pytest.raises(docker_mod.StepError) as failure:
        docker("compose", "up")
    assert str(failure.value) == (
        "docker compose — код 1: url=postgresql://audit_migrate:***@db | secret ***"
    )
    assert docker.raw == [(("compose", "up"), err.decode())]


def test_a172_timeout_keeps_what_the_command_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A172, вторая строка: команда не уложилась в предел — причина с пределом; что она успела
    вывести, лежит в сыром захвате."""

    def slow(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(argv, 30, output=f"half {INVENTED}".encode(), stderr=b"!")

    monkeypatch.setattr(docker_mod.subprocess, "run", slow)
    docker = docker_mod.Docker()
    with pytest.raises(docker_mod.StepError) as failure:
        docker("compose", "logs", limit="docker")
    assert str(failure.value) == "docker compose logs не уложился в 30 с"
    assert docker.raw == [(("compose", "logs"), f"half {INVENTED}!")]


def test_a65_executor_cleanup_names_what_was_not_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A65: исполнитель снимает свои контейнеры и образы по разу на имя; что осталось после снятия
    или на чём команда не уложилась в предел, — названо, остальное снято молча."""
    seen: list[list[str]] = []

    def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[bytes]:
        seen.append(argv[1:])
        if argv[1:3] == ["image", "rm"]:
            raise subprocess.TimeoutExpired(argv, 30)
        left = argv[1:3] == ["ps", "-aq"] and argv[-1] == "name=^c-left$"
        return subprocess.CompletedProcess(argv, 0, b"abc\n" if left else b"", b"")

    monkeypatch.setattr(docker_mod.subprocess, "run", run)
    docker = docker_mod.Docker(containers=["c-left", "c-gone", "c-left"], images=["img-stuck"])
    assert docker.cleanup() == ["c-left", "img-stuck"]
    assert seen == [
        ["rm", "-f", "-v", "c-left"],
        ["ps", "-aq", "--filter", "name=^c-left$"],
        ["rm", "-f", "-v", "c-gone"],
        ["ps", "-aq", "--filter", "name=^c-gone$"],
        ["image", "rm", "-f", "img-stuck"],
    ]


def test_a172_run_puts_every_invented_secret_in_the_mask(tmp_path: Path) -> None:
    """A172: секреты прогона выдумывает оракул — пароль суперпользователя, секрет подписи, пароли
    ролей миграций и сервиса; у роли миграций — с «@»; все не короче 12 знаков (A21). С созданием
    прогона все — в маске исполнителя, пароль роли миграций — и в процентной записи."""
    docker = docker_mod.Docker()
    run = oracle.Run(docker, tmp_path / "copy", "ai-geo-audit-oracle-test", "label", {})
    invented = (run.password, run.jwt, run.migrate_password, run.app_password)
    assert "@" in run.migrate_password
    assert min(len(secret) for secret in invented) >= 12
    assert len(set(invented)) == 4
    assert docker.mask == [
        run.password,
        run.jwt,
        run.migrate_password,
        quote(run.migrate_password, safe=""),
        run.app_password,
    ]


def test_a172_service_log_tail_hides_every_invented_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A172 (A67, L89): хвост журнала сервиса для причины красного — каждый выдуманный секрет
    прогона, и пароль роли миграций в процентной записи, — звёздочками."""
    run = oracle.Run(docker_mod.Docker(), tmp_path / "copy", "ai-geo-audit-oracle-test", "l", {})
    encoded = quote(run.migrate_password, safe="")
    log = (
        f"migrate-1  | {run.migrate_password} {encoded}\n"
        f"migrate-1  | {run.app_password} {run.password} {run.jwt}\n"
    )
    monkeypatch.setattr(compose_mod, "compose", lambda run, *args, **kw: log)
    assert compose_mod.service_log(run, "migrate") == (
        "журнал migrate: migrate-1  | *** *** | migrate-1  | *** *** ***"
    )
