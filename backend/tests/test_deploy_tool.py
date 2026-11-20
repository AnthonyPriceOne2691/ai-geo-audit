"""Скрипт выката, ядро (Ч1.6а, срез 1): исполнитель, журнал хода, замок, функция файлов, файл
сервера, имя сайта, снимок соседа.

Скрипт грузится по пути файла — пакет сервиса он не импортирует. Внешние команды отвечает
подставной исполнитель записями `deploy_records`; настоящий запуск процессов в тестах ядра
запрещён (L38). Без Docker и без сети.
"""

from __future__ import annotations

import ast
import http.server
import importlib.util
import io
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import deploy_records as rec
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "deploy.py"


def load_tool() -> ModuleType:
    """Модуль скрипта по пути файла; в `sys.modules` — до исполнения, этого ждут dataclasses."""
    spec = importlib.util.spec_from_file_location("deploy_tool", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["deploy_tool"] = module
    spec.loader.exec_module(module)
    return module


tool = load_tool()
Refused = tool.RefusedError
PRUNE = {(kind, "prune") for kind in ("system", "volume", "image", "container", "network")}
STOPS = {"stop", "kill", "rm", "restart", "pause"}


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
        self.ps_code = 0
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
        if argv == tool.PS_ALL:
            return tool.Result(self.ps_code, "".join(f"{key}\n" for key in self.lines), "")
        if argv[:5] == tool.INSPECT:
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
    """Файл сервера после `env-init` и `put-secret`: значения выдуманы, адреса — RFC 5737."""
    migrate, app, redis = over.pop("migrate", "a1" * 24), "b2" * 24, over.pop("redis", "c3" * 24)
    return {
        "POSTGRES_MIGRATE_PASSWORD": migrate,
        "POSTGRES_APP_PASSWORD": app,
        "REDIS_PASSWORD": redis,
        "JWT_SECRET": "d4" * 32,
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


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Корни — во временном каталоге, исполнитель и проба — подставные; настоящий запуск
    процессов запрещён (L38)."""

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


def test_a186_substitution_is_in_place(ctx) -> None:
    """A186 (L38): исполнитель и проба — подставные, настоящий процесс запрещён; команда без
    записи — красное, а не тихий ответ."""
    assert isinstance(ctx.runner, FakeRunner) and isinstance(ctx.probe, FakeProbe)
    with pytest.raises(AssertionError, match="настоящий процесс"):
        tool.Runner().run(["true"])
    with pytest.raises(AssertionError, match="без записи"):
        ctx.runner.run(["docker", "info"])


def test_a186_snapshot_unchanged_neighbor(ctx) -> None:
    """A186: снимок до и после без перемен — «общих N, перезапусков 0, коды прежние; пропало 0,
    новых 0», не красное. Наш стек не входит; `StartedAt` с дробной частью 6, 8 и 9 знаков и
    миграции с узлом здоровья и без него сравниваются как есть; адреса опрошены оба раза."""
    started = [re.search(r'"StartedAt":"[^"]+\.(\d+)Z"', line) for line in rec.server_machine()]
    assert {len(found[1]) for found in started if found} == {6, 8, 9}
    write_env(ctx, server_values())
    result = check_like(ctx)
    line = "снимок соседа: общих 35, перезапусков 0, коды прежние; пропало 0, новых 0"
    assert (result.line, result.red) == (line, False) and line in ctx.out.getvalue()
    assert ctx.probe.urls == URLS.split(",") * 2


def test_a186_snapshot_divergence_is_red_with_numbers_not_names(ctx) -> None:
    """A186: у общих — перезапуск, пересоздание и смена здоровья — красное с числами, без имён;
    пропавший и новый — строкой, сами по себе не красное."""
    write_env(ctx, server_values())
    after = changed(rec.server_machine(), "nb-alpha-web-1", started="2026-11-20T09:00:00.2Z")
    after = changed(after, "nb-alpha-web-1", restarts=1)
    after = changed(after, "nb-alpha-postgres-1", health="unhealthy")
    after = changed(after, "nb-beta-api-1", id="f" * 64)
    after = swap(after, "nb-alpha-migrate-1", rec.inspect_line("nb-alpha-cron-1", "nb-alpha"))
    result = check_like(ctx, after)
    assert result.red and result.line == (
        "снимок соседа: общих 34, перезапусков 2, коды прежние; пропало 1, новых 1"
        " — красное: Id 1, StartedAt 1, RestartCount 1, здоровье 1"
    )
    assert "nb-" not in ctx.out.getvalue()
    ctx.runner.become(rec.server_machine())
    run = rec.inspect_line("nb-beta-run-1", "nb-beta")
    result = check_like(ctx, swap(rec.server_machine(), "nb-beta-migrate-1", run))
    assert (result.red, result.line.endswith("пропало 1, новых 1")) == (False, True)


def test_a186_changed_health_code_is_red(ctx) -> None:
    """A186: код адреса проверки соседа сменился между снимками — красное, адрес не назван."""
    with tool.session(ctx, "check"):
        before = tool.neighbor_snapshot(ctx, URLS)
        ctx.probe.codes["http://127.0.0.1:9102/health"] = 403
        result = tool.compare(before, tool.neighbor_snapshot(ctx, URLS))
    tail = "кодов изменилось 1; пропало 0, новых 0 — красное: коды адресов 1"
    assert result.red and result.line.endswith(tail)
    assert "9102" not in result.line


@pytest.mark.parametrize(
    "url",
    [
        "http://192.0.2.10:9101/health",
        "http://example.test:9101/health",
        "http://localhost:9101/health",
        "https://127.0.0.1:9101/health",
        "http://user:pass@127.0.0.1:9101/health",
        "http://127.0.0.1:99999/health",
        "127.0.0.1:9101/health",
    ],
)
def test_a186_address_not_on_loopback_refused_before_any_request(ctx, url: str) -> None:
    """A186: адрес проверки соседа не на петле — отказ до запроса и до первой команды; в тексте
    отказа — номер адреса, а не он сам."""
    with pytest.raises(Refused, match="адрес № 2 не на петле") as refused:
        tool.neighbor_snapshot(ctx, f"http://127.0.0.1:9101/,{url}")
    assert url not in str(refused.value) and "9101" not in str(refused.value)
    assert (ctx.probe.urls, ctx.runner.calls) == ([], [])


def test_a186_container_gone_between_listing_and_inspect_is_gone_not_failure(ctx) -> None:
    """A186: контейнер удалён между `docker ps` и `docker container inspect` — он «пропал»; сбой
    `docker ps` и чужой формат ответа — отказ словами."""
    before = tool.neighbor_snapshot(ctx, "")
    ctx.runner.vanish = {rec.cid("nb-alpha-svc-a-1"), rec.cid("ai-geo-audit-api-1")}
    assert tool.compare(before, tool.neighbor_snapshot(ctx, "")).line.endswith("пропало 1, новых 0")
    ctx.runner.become(rec.server_machine()[:1])
    ctx.runner.lines[rec.cid("nb-alpha-scaled-1")] = '{"id":"x"}'
    with pytest.raises(Refused, match="не разобран — сверить версию docker"):
        tool.neighbor_snapshot(ctx, "")
    ctx.runner.ps_code = 1
    with pytest.raises(Refused, match="docker ps: код 1"):
        tool.neighbor_snapshot(ctx, "")


#: A186: строка на каждую запрещённую команду (L37).
FORBIDDEN = [
    ("docker", "system", "prune", "--all", "--force"),
    ("docker", "volume", "prune", "--force"),
    ("docker", "image", "prune", "--all", "--force"),
    ("docker", "container", "prune", "--force"),
    ("docker", "network", "prune", "--force"),
    ("docker", "stop", rec.cid("nb-alpha-web-1"), rec.cid("ai-geo-audit-api-1")),
    ("docker", "kill", rec.cid("nb-alpha-postgres-1")),
    ("docker", "rm", "--force", rec.cid("nb-beta-migrate-1")),
    ("docker", "restart"),
    ("docker", "container", "stop", rec.cid("nb-beta-web-1")),
    ("docker", "pause", rec.cid("nb-alpha-scaled-1")),
    ("docker", "compose", "up", "--detach"),
    ("docker", "compose", "-p", "nb-alpha", "down"),
    ("docker", "compose", "--project-name", "nb-beta", "ps"),
]
LAWFUL = [tool.PS_ALL, ("docker", "stop", rec.cid("ai-geo-audit-api-1"))]
LAWFUL += [("docker", "compose", "--project-name", "ai-geo-audit", "ps")]


@pytest.mark.parametrize("argv", FORBIDDEN)
def test_a186_forbidden_command_is_red(ctx, argv: tuple[str, ...]) -> None:
    """A186: prune, остановка или удаление не своего, compose не своего стека — красное."""
    with pytest.raises(AssertionError, match="запрещённая команда"):
        ctx.runner.run(argv)


@pytest.mark.parametrize("argv", LAWFUL)
def test_a186_lawful_command_passes_the_table(argv: tuple[str, ...]) -> None:
    """A186: своя команда таблицей запрещённых не судится."""
    assert forbidden(argv, {rec.cid("ai-geo-audit-api-1")}) is None


#: A186: строка на каждый случай пути (L37): операция, путь от временного корня, своё ли.
ROOT_CASES = [
    ("write", "srv/ai-geo-audit/.env", True),
    ("write", "srv/ai-geo-audit/.deploy/x.log", True),
    ("write", "srv/ai-geo-audit-backups/x.dump", True),
    ("write", "etc/ai-geo-audit/master.key", True),
    ("remove", "srv/ai-geo-audit-backups/old.dump", True),
    ("remove", "srv/ai-geo-audit-backups/../x.dump", False),
    ("remove", "srv/ai-geo-audit-backups", False),
    ("write", "srv/nb-alpha/docker-compose.yml", False),
    ("write", "srv/ai-geo-audit-backups-old/x.dump", False),
    ("write", "etc/nginx/sites-enabled/nb-alpha", False),
    ("write", "srv/ai-geo-audit/link/docker-compose.yml", False),
    ("rename", "srv/x.dump", False),
    ("mkdir", "etc/ai-geo-audit", True),
    ("mkdir", "etc/ai-geo-audit-old", False),
    ("append", "srv/nb-alpha/x.log", False),
]


@pytest.mark.parametrize(("op", "rel", "own"), ROOT_CASES)
def test_a186_files_only_under_own_roots(ctx, tmp_path: Path, op: str, rel: str, own: bool) -> None:
    """A186: запись, переименование, удаление и каталоги — только под своими корнями; путь наружу
    через `..` (ротация копий уровнем выше своего корня) и через ссылку — отказ, чужое цело."""
    for name in ("srv/nb-alpha", "etc/nginx/sites-enabled", "srv/ai-geo-audit-backups"):
        (tmp_path / name).mkdir(parents=True)
    for name in (
        "srv/x.dump",
        "srv/ai-geo-audit-backups/old.dump",
        "srv/nb-alpha/docker-compose.yml",
    ):
        (tmp_path / name).write_text("чужое", encoding="utf-8")
    (ctx.paths.service / ".deploy").mkdir()
    (ctx.paths.service / "link").symlink_to(tmp_path / "srv/nb-alpha")
    path = tmp_path / rel
    if own and op == "write":
        path.parent.mkdir(parents=True, exist_ok=True)
    act = {
        "write": lambda: ctx.files.write(path, b"x"),
        "remove": lambda: ctx.files.remove(path),
        "rename": lambda: ctx.files.rename(path, ctx.paths.backups / "x.dump"),
        "mkdir": lambda: ctx.files.mkdir(path),
        "append": lambda: ctx.files.append(path, "x\n"),
    }[op]
    if own:
        act()
        assert ctx.files.ops[-1][:2] == (op, str(path))
        return
    with pytest.raises(Refused, match="вне своих корней"):
        act()
    assert ctx.files.ops == []
    others = ("srv/x.dump", "srv/nb-alpha/docker-compose.yml")
    assert [(tmp_path / name).read_text(encoding="utf-8") for name in others] == ["чужое"] * 2


def test_a186_temp_file_is_600_from_creation_and_crash_keeps_old_file(ctx, monkeypatch) -> None:
    """A186: временный файл создаётся сразу с правами 600 и только новым, права итога — заданные;
    обрыв до переименования прежний файл не портит и временного не оставляет."""
    target, opened, real_open = ctx.paths.env_file, [], os.open
    target.write_text("прежнее\n", encoding="utf-8")

    def spy(path, flags, mode=0o777, **kwargs):
        opened.append((Path(path), flags, mode))
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(tool.os, "open", spy)
    ctx.files.write(target, b"new\n", mode=0o400)
    created = [(path, flags, mode) for path, flags, mode in opened if flags & os.O_CREAT]
    assert len(created) == 1 and created[0][2] == 0o600 and created[0][1] & os.O_EXCL
    assert created[0][0].parent == target.parent and created[0][0].name.startswith(".")
    assert target.stat().st_mode & 0o777 == 0o400
    target.chmod(0o600)
    target.write_text("прежнее\n", encoding="utf-8")

    def crash(*_args: object) -> None:
        raise OSError("обрыв")

    monkeypatch.setattr(tool.os, "replace", crash)
    with pytest.raises(OSError, match="обрыв"):
        ctx.files.write(target, b"new\n")
    assert target.read_text(encoding="utf-8") == "прежнее\n"
    assert sorted(p.name for p in target.parent.iterdir()) == [".env"]


#: A186: вызовы, что меняют диск, — только в классе `Files`; элемент на каждый (L37).
WRITERS = [
    "os.replace(a, b)",
    "os.rename(a, b)",
    "os.unlink(a)",
    "os.remove(a)",
    "os.rmdir(a)",
    "os.mkdir(a)",
    "os.makedirs(a)",
    "os.chmod(a, 0)",
    "os.chown(a, 0, 0)",
    "os.ftruncate(a, 0)",
    "os.pwrite(a, b, 0)",
    "os.write(a, b)",
    "os.symlink(a, b)",
    "os.fdopen(a, 'w')",
    "os.open(a, os.O_WRONLY)",
    "os.open(a, os.O_RDWR | os.O_CREAT)",
    "shutil.rmtree(a)",
    "shutil.move(a, b)",
    "shutil.copyfile(a, b)",
    "open(a, 'w')",
    "open(a, mode='a')",
    "a.write_text('x')",
    "a.write_bytes(b'x')",
    "a.unlink()",
    "a.rename(b)",
    "a.touch()",
    "a.mkdir()",
    "a.rmdir()",
    "a.symlink_to(b)",
    "a.chmod(0)",
]
MODULE_WRITERS = {call.split("(")[0] for call in WRITERS if call.startswith(("os.", "shutil."))}
PATH_WRITERS = {call.split("(")[0][2:] for call in WRITERS if call.startswith("a.")}


def changes_disk(call: ast.Call) -> bool:
    """A186: функция `os`/`shutil` из списка (`os.open` — с флагами записи), `open` с режимом
    записи, метод пути из списка — не через функцию файлов `….files.`."""
    name, text = ast.unparse(call.func), ast.unparse(call)
    if name in MODULE_WRITERS:
        return name != "os.open" or any(flag in text for flag in ("O_WRONLY", "O_RDWR", "O_CREAT"))
    if name == "open":
        modes = [ast.unparse(arg) for arg in call.args[1:2]]
        modes += [ast.unparse(k.value) for k in call.keywords if k.arg == "mode"]
        return any(set(mode) & set("wax+") for mode in modes)
    method = call.func.attr if isinstance(call.func, ast.Attribute) else ""
    return method in PATH_WRITERS and not name.endswith(f"files.{method}")


def writers_outside_files(source: str) -> list[str]:
    """A186: вызовы, что меняют диск, вне класса `Files`."""
    found: list[str] = []

    def visit(node: ast.AST, inside: bool) -> None:
        inside = node.name == "Files" if isinstance(node, ast.ClassDef) else inside
        if isinstance(node, ast.Call) and not inside and changes_disk(node):
            found.append(ast.unparse(node))
        for child in ast.iter_child_nodes(node):
            visit(child, inside)

    visit(ast.parse(source), False)
    return found


@pytest.mark.parametrize("call", WRITERS)
def test_a186_disk_writer_outside_the_file_function_is_found(call: str) -> None:
    """A186: проба детектора — вызов, меняющий диск, вне `Files` найден, внутри — нет."""
    assert writers_outside_files(f"def f(a, b):\n    {call}\n") == [call]
    assert writers_outside_files(f"class Files:\n    def f(a, b):\n        {call}\n") == []


def test_a186_script_writes_to_disk_only_through_the_file_function() -> None:
    """A186: в скрипте нет вызова, меняющего диск, вне функции файлов; чтение и `ctx.files` —
    не находки."""
    assert writers_outside_files(SCRIPT.read_text(encoding="utf-8")) == []
    lawful = (
        "def f(a, ctx):\n    open(a).read()\n    os.open(a, os.O_RDONLY)\n    ctx.files.mkdir(a)\n"
    )
    assert writers_outside_files(lawful) == []


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("example.test", "ai-geo-audit.example.test"),
        ("", "пусто"),
        ("*.example.test", "звёздочка"),
        ("example.test.", "точка в конце"),
        ("example.test/site", "«/» и пробелы"),
        ("example test", "«/» и пробелы"),
        (".".join(["c" * 60] * 4), "длиннее 253"),
        ("b" * 64 + ".example.test", "длиннее 63"),
        ("exa_mple.test", "латинские буквы, цифры и дефис"),
    ],
)
def test_a184_site_name_from_server_file_only(domain: str, expected: str) -> None:
    """A184: имя сайта — `ai-geo-audit.<SITE_DOMAIN>`; негодный домен — отказ без значения."""
    if expected.startswith("ai-geo-audit."):
        assert tool.site_name(domain) == expected
        return
    with pytest.raises(Refused, match=expected) as refused:
        tool.site_name(domain)
    assert not domain or domain not in str(refused.value)


def test_a184_bad_domain_refused_before_any_command(ctx) -> None:
    """A184: негодный `SITE_DOMAIN` — отказ до любой внешней команды и проб соседа; домена в
    скрипте нет — только в файле сервера."""
    write_env(ctx, server_values(SITE_DOMAIN="*.example.test"))
    with pytest.raises(Refused, match="звёздочка"):
        check_like(ctx)
    assert (ctx.runner.calls, ctx.probe.urls) == ([], [])
    assert "example.test" not in SCRIPT.read_text(encoding="utf-8")


def test_a185_server_file_rights_owner_placeholders_and_empties(ctx) -> None:
    """A185 (ядро): права шире 600, владелец не root, заглушка и пустое — каждое названо именем
    и причиной, без значения; «пишет выкат» до первого выката — не отказ."""
    write_env(ctx, server_values(JWT_SECRET="CHANGE_ME", redis=""), mode=0o644)
    env = tool.read_env(ctx.paths.env_file)
    problems = tool.env_problems(env, owner=os.getuid() + 1, may_be_empty=frozenset({"WEB_IMAGE"}))
    assert problems == [
        "права файла окружения шире 600 — chmod 600",
        "владелец файла окружения не root — chown root",
        "REDIS_PASSWORD: пусто",
        "JWT_SECRET: заглушка CHANGE_ME",
        "BACKEND_IMAGE: пусто",
    ]
    assert not any("a1a1" in problem or "b2b2" in problem for problem in problems)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SITE_DOMAIN=example.test\nsecret-without-name\n", "строка 2: не «ИМЯ=значение»"),
        ("=secret-without-name\n", "строка 1: не «ИМЯ=значение»"),
        ("WEB_PORT=1\nWEB_PORT=2\n", "имя WEB_PORT дважды"),
        ("lower=1\n", "строка 1: не «ИМЯ=значение»"),
    ],
)
def test_a185_server_file_read_without_execution(ctx, text: str, expected: str) -> None:
    """A185 (ядро): файл сервера — строки «ИМЯ=значение», без исполнения; негодная строка —
    номером, без текста; нет файла и ссылка вместо файла — отказ словами."""
    ctx.paths.env_file.write_text(text, encoding="utf-8")
    with pytest.raises(Refused, match=expected) as refused:
        tool.read_env(ctx.paths.env_file)
    assert "secret-without-name" not in str(refused.value)
    ctx.paths.env_file.unlink()
    with pytest.raises(Refused, match="файла окружения нет — сначала env-init"):
        tool.read_env(ctx.paths.env_file)
    ctx.paths.env_file.symlink_to(ctx.paths.service / "other")
    with pytest.raises(Refused, match="не обычный файл"):
        tool.read_env(ctx.paths.env_file)


def test_lock_second_run_refused_at_once_stale_mark_named(ctx) -> None:
    """Прогоны, вопросы 5 и 6: второй запуск разом — отказ сразу, с номером процесса и временем;
    метка мёртвого процесса — устаревшая, названа и снята; `last` говорит, идёт ли выкат."""
    lock, pid = ctx.paths.deploy_dir / "lock", os.getpid()
    assert tool.main(["last"], ctx) == 0
    assert ctx.out.getvalue() == "журналов хода нет\nвыката нет\n"
    ctx.paths.deploy_dir.mkdir()
    fd, _ = ctx.files.lock(lock)
    try:
        with (
            pytest.raises(Refused, match=rf"идёт выкат: процесс {pid} с 20\d\d-"),
            tool.session(ctx, "check"),
        ):
            pass
        assert tool.lock_state(lock).startswith(f"идёт выкат: процесс {pid} с ")
    finally:
        ctx.files.unlock(fd)
    assert tool.lock_state(lock) == "выката нет"
    lock.write_text("99999999 2026-11-20T00:00:00Z\n", encoding="utf-8")
    assert tool.lock_state(lock).startswith("устаревшая метка: процесс 99999999")
    with tool.session(ctx, "check"):
        pass
    ctx.out = io.StringIO()
    assert tool.main(["last"], ctx) == 0
    shown = ctx.out.getvalue()
    assert "устаревшая метка: процесс 99999999 с 2026-11-20T00:00:00Z — процесс не живёт" in shown
    assert shown.endswith("итог: ок\nвыката нет\n") and "-check.log: строк " in shown


def test_journal_written_first_and_lost_terminal_does_not_stop_the_step(ctx) -> None:
    """Прогоны, вопрос 4: строка хода — сначала в журнал `.deploy/` с правами 600, время — UTC
    с Z; терминал оборвался — шаг идёт дальше, журнал полный."""

    class Gone(io.StringIO):
        def write(self, _text: str) -> int:
            raise BrokenPipeError(32, "Broken pipe")

    ctx.out = Gone()
    with tool.session(ctx, "check") as rep:
        rep.line("шаг после обрыва")
    (log,) = ctx.paths.deploy_dir.glob("*-check.log")
    stamps, texts = zip(*(line.split(" ", 1) for line in log.read_text("utf-8").splitlines()))
    assert texts == ("check: начало", "шаг после обрыва", "итог: ок")
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp) for stamp in stamps)
    assert log.stat().st_mode & 0o777 == 0o600


def test_hangup_is_ignored_from_the_terminal_entry(ctx, monkeypatch) -> None:
    """Прогоны, вопрос 4: вход с терминала глушит SIGHUP — обрыв ssh шаг не роняет."""
    seen: list[tuple[int, object]] = []
    monkeypatch.setattr(tool.signal, "signal", lambda sig, handler: seen.append((sig, handler)))
    assert tool.entry(["last"], ctx) == 0
    assert seen == [(signal.SIGHUP, signal.SIG_IGN)]


#: A183: метки — значения, которых в выводе, журнале и аргументах быть не должно.
MARKS = [f"mkq{n:02d}" for n in range(1, 16)] + ["4791234567", "198.51.100.208", "nb-alpha"]
MARKS += ["198.51.100.210", "nb-beta", *(str(port) for port in range(47911, 47917))]


def test_a183_no_mark_in_output_journal_or_arguments(ctx, capsys) -> None:
    """A183: метки в секретах файла окружения, мастер-ключе, входящем файле, пароле сайта, в
    домене, порте, подсети, адресах, в именах и портах соседа; ядро по удачным сценариям и с
    отказами — меток нет ни в выводе, ни в журнале `.deploy/`, ни в аргументах команд."""
    values = server_values(
        migrate="a1a1mkq01",
        POSTGRES_APP_PASSWORD="b2b2mkq02",
        API_DATABASE_URL="postgresql+asyncpg://app:b2b2mkq02@db:5432/a",
        redis="c3c3mkq03",
        JWT_SECRET="d4d4mkq04",
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
        values | {"JWT_SECRET": "CHANGE_ME"},
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


def test_a183_runner_values_only_through_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A183 (исполнитель): значение уходит команде окружением процесса, не аргументом; stdin
    пустой, окружение оператора не передаётся, дочерний процесс — в своей сессии."""
    monkeypatch.setenv("OPERATOR_MARK", "mkq17")
    code = (
        "import os, sys; print(sys.stdin.read() == '', os.environ.get('MARK'), "
        "'OPERATOR_MARK' in os.environ, os.getsid(0) != int(sys.argv[1]))"
    )
    argv = [sys.executable, "-I", "-c", code, str(os.getsid(0))]
    result = tool.Runner().run(argv, env={"MARK": "mkq16"})
    assert (result.code, result.out.split()) == (0, ["True", "mkq16", "False", "True"])
    assert not any("mkq16" in arg for arg in argv)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_runner_time_limit_kills_the_whole_group(tmp_path: Path) -> None:
    """Прогоны, вопрос 3: у внешней команды предел времени; по пределу гаснет вся группа — и
    процесс, порождённый командой; отказ называет команду, без её аргументов."""
    pidfile = tmp_path / "child.pid"
    code = (
        "import subprocess, sys, time; "
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"f = open({str(pidfile)!r}, 'w'); f.write(str(p.pid)); f.close(); time.sleep(60)"
    )
    started = time.monotonic()
    with pytest.raises(Refused, match=r"-I: не уложилась в 2 с") as refused:
        tool.Runner().run([sys.executable, "-I", "-c", code], timeout=2)
    assert time.monotonic() - started < 10 and "sleep" not in str(refused.value)
    deadline, child = time.monotonic() + 5, int(pidfile.read_text())
    while time.monotonic() < deadline and alive(child):
        time.sleep(0.05)
    assert not alive(child)


@pytest.fixture
def local_server() -> Iterator[str]:
    """HTTP на петле: `/moved` — 302, остальное — 503."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302 if self.path == "/moved" else 503)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()

        def log_message(self, *_args) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_a186_health_probe_reads_code_without_following_redirects(local_server: str) -> None:
    """A186: проба адреса соседа отдаёт код как есть — по перенаправлению не идёт; закрытый
    порт — 0, «нет ответа»."""
    assert tool.http_code(local_server + "/moved") == 302
    assert tool.http_code(local_server + "/health?full=1") == 503
    assert tool.http_code("http://127.0.0.1:1/health", timeout=2) == 0
