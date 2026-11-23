"""Общее тестов скрипта выката (Ч1.6а): загрузка скрипта по пути, подставные исполнитель и
проба, файл сервера, сценарий ядра «как зовут `check`, `up` и `proxy install`».

Скрипт грузится по пути файла — пакет сервиса он не импортирует. Внешние команды отвечает
подставной исполнитель записями `deploy_records`; настоящий запуск процессов в тестах ядра
запрещён (L38). Без Docker и без сети.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import deploy_records as rec
import envguard
import pytest

#: Корень копии — по меткам (`envguard.repo_root`), а не по глубине: под mutmut тесты лежат в
#: `backend/mutants/tests`, и счёт от этого файла давал `backend/`.
ROOT = envguard.ROOT

SCRIPT = ROOT / "deploy" / "deploy.py"


def load_tool() -> ModuleType:
    """Модуль скрипта по пути файла; в `sys.modules` — до исполнения, этого ждут dataclasses."""
    spec = importlib.util.spec_from_file_location("deploy_tool", SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"скрипт выката не загружается: {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["deploy_tool"] = module
    spec.loader.exec_module(module)
    return module


tool = load_tool()

Refused = tool.RefusedError

PRUNE = {(kind, "prune") for kind in ("system", "volume", "image", "container", "network")}

STOPS = {"stop", "kill", "rm", "restart", "pause"}
PS_FLAGS = {"--all", "--quiet", "--no-trunc"}


def forbidden(argv: tuple[str, ...], ours: set[str]) -> str | None:
    """A186: правило, которое нарушает команда; `None` — команда законна."""
    rest = argv[1:] if argv[:1] == ("docker",) else ()
    verb_at = 1 if rest[:1] == ("container",) else 0
    if tuple(rest[:2]) in PRUNE:
        return "prune"
    if len(rest) > verb_at and rest[verb_at] in STOPS:
        targets = {arg for arg in rest[verb_at + 1 :] if not arg.startswith("-")}
        return None if targets and targets <= ours else "остановка или удаление не своего"
    if rest[:1] == ("compose",) and tuple(rest[1:3]) != ("--project-name", "ai-geo-audit"):
        return "compose не своего стека"
    return None


class FakeRunner:
    """Подставной исполнитель: отвечает записями, пишет каждую команду; команда без записи и
    запрещённая (A186) — красное в момент вызова, то есть во всех сценариях тестов."""

    def __init__(self, lines: list[str]) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
        self.vanish: set[str] = set()
        self.ps_code, self.ps_out, self.ps_err, self.inspect_err = 0, None, "", ""
        self.become(lines)

    def become(self, lines: list[str]) -> None:
        """Состояние машины к следующей команде."""
        self.lines = {json.loads(line)["id"]: line for line in lines}
        project = "com.docker.compose.project"
        owned = {key: json.loads(line)["labels"].get(project) for key, line in self.lines.items()}
        self.ours = {key for key, name in owned.items() if name == "ai-geo-audit"}

    def run(self, argv, *, timeout: float = 30.0, env=None):
        argv = tuple(argv)
        self.calls.append((argv, dict(env or {})))
        if rule := forbidden(argv, self.ours):
            raise AssertionError(f"запрещённая команда ({rule}): {' '.join(argv[:3])}")
        flags = set(argv[2:])
        if argv[:2] == ("docker", "ps") and {"--quiet", "--no-trunc"} <= flags <= PS_FLAGS:
            # Как настоящий docker: без `--all` остановленных в ответе нет.
            running = {
                key
                for key, line in self.lines.items()
                if json.loads(line).get("state", {}).get("Running", True)
            }
            listed = [key for key in self.lines if "--all" in flags or key in running]
            out = "".join(f"{key}\n" for key in listed) if self.ps_out is None else self.ps_out
            return tool.Result(self.ps_code, out, self.ps_err)
        if argv[:4] == ("docker", "container", "inspect", "--format"):
            if self.inspect_err:
                return tool.Result(1, "", self.inspect_err)
            found = [key for key in argv[5:] if key in self.lines and key not in self.vanish]
            lost = [key for key in argv[5:] if key not in found]
            err = "".join(f"Error response from daemon: No such container: {k}\n" for k in lost)
            return tool.Result(int(bool(lost)), "".join(self.lines[k] + "\n" for k in found), err)
        raise AssertionError("команда без записи: " + " ".join(argv[:3]))


#: Разведка, п. 3: два-четыре адреса `http://127.0.0.1:<порт веба соседа>/<путь>`, коды разные.
URLS = "http://127.0.0.1:9101/,http://127.0.0.1:9102/health"


class FakeProbe:
    """Подставная проба адресов соседа: пишет каждый адрес, отвечает кодом из таблицы."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.codes = dict(zip(URLS.split(","), (401, 200), strict=True))

    def __call__(self, url: str) -> int:
        self.urls.append(url)
        return self.codes.get(url, 404)


def server_values(**over: str) -> dict[str, str]:
    """Файл сервера после `env-init` и `put-secret`: значения выдуманы, адреса — RFC 5737.
    Пароли ролей, очереди и секрет подписи — именами `migrate`, `app`, `redis`, `signing`: адреса
    несут те же пароли."""
    migrate, app = over.pop("migrate", "a1" * 24), over.pop("app", "b2" * 24)
    redis, signing = over.pop("redis", "c3" * 24), over.pop("signing", "d4" * 32)
    return {
        "POSTGRES_MIGRATE_PASSWORD": migrate,
        "POSTGRES_APP_PASSWORD": app,
        "REDIS_PASSWORD": redis,
        "JWT_SECRET": signing,
        "MIGRATE_DATABASE_URL": f"postgresql+asyncpg://migrator:{migrate}@db:5432/a",
        "API_DATABASE_URL": f"postgresql+asyncpg://app:{app}@db:5432/a",
        "API_REDIS_URL": f"redis://:{redis}@redis:6379/0",
        "ALERT_TELEGRAM_BOT_TOKEN": "1234567:token-from-owner",
        "ALERT_TELEGRAM_CHAT_ID": "-1001234567",
        "SITE_DOMAIN": "example.test",
        "WEB_PORT": "18100",
        "FRONT_SUBNET": "198.51.100.0/28",
        "WEB_ADDRESS": "198.51.100.2",
        "NEIGHBOR_HEALTH_URLS": URLS,
        "BACKEND_IMAGE": "",
        "WEB_IMAGE": "",
    } | over


def write_env(ctx, values: dict[str, str], mode: int = 0o600) -> None:
    ctx.paths.env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()), "utf-8")
    ctx.paths.env_file.chmod(mode)


def changed(lines: list[str], name: str, **changes: object) -> list[str]:
    """Машина, где у контейнера `name` изменены поля записи."""
    key = rec.cid(name)
    return [rec.vary(line, **changes) if json.loads(line)["id"] == key else line for line in lines]


def swap(lines: list[str], name: str, line: str) -> list[str]:
    """Машина, где контейнер `name` пропал, а вместо него появился другой."""
    return [item for item in lines if json.loads(item)["id"] != rec.cid(name)] + [line]


def make_ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Контекст скрипта: корни — во временном каталоге, исполнитель и проба — подставные;
    настоящий запуск процессов запрещён (L38)."""

    def no_process(*_args, **_kwargs):
        raise AssertionError("настоящий процесс в тесте ядра скрипта выката")

    monkeypatch.setattr(tool.subprocess, "Popen", no_process)
    srv, etc = tmp_path / "srv", tmp_path / "etc"
    paths = tool.Paths(srv / "ai-geo-audit", srv / "ai-geo-audit-backups", etc / "ai-geo-audit")
    paths.service.mkdir(parents=True)
    runner = FakeRunner(rec.server_machine() + rec.ours())
    return tool.Context(paths, runner, FakeProbe(), io.StringIO(), os.getuid())


def check_like(ctx, after: list[str] | None = None) -> tool.Comparison:
    """Как ядро зовут `check`, `up` и `proxy install`: файл сервера и имя сайта, снимок соседа
    до и после; между снимками машина может перейти в состояние `after`."""
    with tool.session(ctx, "check") as rep:
        env = tool.server_env(ctx, may_be_empty=frozenset({"BACKEND_IMAGE", "WEB_IMAGE"}))
        urls = env.values["NEIGHBOR_HEALTH_URLS"]
        before = tool.neighbor_snapshot(ctx, urls)
        if after is not None:
            ctx.runner.become(after)
        result = tool.compare(before, tool.neighbor_snapshot(ctx, urls))
        rep.line(result.line)
    return result
