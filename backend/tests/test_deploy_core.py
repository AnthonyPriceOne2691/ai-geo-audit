"""Скрипт выката, ядро (Ч1.6а): исполнитель, журнал хода, замок, функция файлов и свои корни,
запрещённые команды, файл сервера, имя сайта."""

from __future__ import annotations

import ast
import io
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Any

import deploy_records as rec
import pytest
from deploy_fakes import (
    SCRIPT,
    FakeProbe,
    FakeRunner,
    Refused,
    check_like,
    forbidden,
    make_ctx,
    server_values,
    tool,
    write_env,
)


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Корни — во временном каталоге, исполнитель и проба — подставные (L38)."""
    return make_ctx(tmp_path, monkeypatch)


def test_a186_substitution_is_in_place(ctx) -> None:
    """A186 (L38): исполнитель и проба — подставные, настоящий процесс запрещён; команда без
    записи — красное, а не тихий ответ."""
    assert isinstance(ctx.runner, FakeRunner) and isinstance(ctx.probe, FakeProbe)
    with pytest.raises(AssertionError, match="настоящий процесс"):
        tool.Runner().run(["true"])
    with pytest.raises(AssertionError, match="без записи"):
        ctx.runner.run(["docker", "info"])


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
    ("mkdir", "srv/ai-geo-audit/link", False),
    ("append", "srv/ai-geo-audit/file-link", False),
]


@pytest.mark.parametrize(("op", "rel", "own"), ROOT_CASES)
def test_a186_files_only_under_own_roots(ctx, tmp_path: Path, op: str, rel: str, own: bool) -> None:
    """A186: запись, переименование, удаление и каталоги — только под своими корнями; путь наружу
    через `..` (ротация копий уровнем выше своего корня) и через ссылку — в середине пути и в
    последнем звене — отказ; чужое цело, права чужого каталога прежние."""
    for name in ("srv/nb-alpha", "etc/nginx/sites-enabled", "srv/ai-geo-audit-backups"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "srv/nb-alpha").chmod(0o755)
    for name in (
        "srv/x.dump",
        "srv/ai-geo-audit-backups/old.dump",
        "srv/nb-alpha/docker-compose.yml",
    ):
        (tmp_path / name).write_text("чужое", encoding="utf-8")
    (ctx.paths.service / ".deploy").mkdir()
    (ctx.paths.service / "link").symlink_to(tmp_path / "srv/nb-alpha")
    (ctx.paths.service / "file-link").symlink_to(tmp_path / "srv/x.dump")
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
    with pytest.raises(Refused, match="вне своих корней|ссылка"):
        act()
    assert ctx.files.ops == []
    others = ("srv/x.dump", "srv/nb-alpha/docker-compose.yml")
    assert [(tmp_path / name).read_text(encoding="utf-8") for name in others] == ["чужое"] * 2
    assert (tmp_path / "srv/nb-alpha").stat().st_mode & 0o777 == 0o755


def test_a186_a183_links_are_not_followed(ctx, tmp_path: Path, capsys) -> None:
    """A186, A183: `.deploy` — ссылка на чужой каталог: `mkdir` и запуск команды — отказ, права
    чужого каталога прежние; `last` — отказ, строки чужого журнала в выводе нет. Свой корень —
    ссылка: запись и `mkdir` — отказ. Цель `mkdir` — не каталог: отказ. Замок — ссылка на чужой
    файл в настоящем `.deploy`: `last` — отказ, чужой метки в выводе нет (порча N6)."""
    foreign = tmp_path / "srv/nb-alpha"
    foreign.mkdir()
    foreign.chmod(0o755)
    (foreign / "neighbor.log").write_text("строка чужого журнала\n", encoding="utf-8")
    ctx.paths.deploy_dir.symlink_to(foreign)
    with pytest.raises(Refused, match=r"\.deploy — ссылка"):
        ctx.files.mkdir(ctx.paths.deploy_dir)
    with pytest.raises(Refused, match="ссылка"), tool.session(ctx, "check"):
        pass
    assert foreign.stat().st_mode & 0o777 == 0o755
    assert tool.main(["last"], ctx) == 1
    shown = ctx.out.getvalue() + "".join(capsys.readouterr())
    assert "чужого журнала" not in shown and "neighbor.log" not in shown and "ссылка" in shown
    ctx.paths.backups.symlink_to(foreign)
    for act in (
        lambda: ctx.files.write(ctx.paths.backups / "x.dump", b"x"),
        lambda: ctx.files.mkdir(ctx.paths.backups),
    ):
        with pytest.raises(Refused, match="ссылка"):
            act()
    assert sorted(p.name for p in foreign.iterdir()) == ["neighbor.log"]
    ctx.paths.etc.parent.mkdir()
    ctx.paths.etc.write_text("не каталог", encoding="utf-8")
    with pytest.raises(Refused, match="не каталог"):
        ctx.files.mkdir(ctx.paths.etc)
    ctx.paths.deploy_dir.unlink()
    ctx.paths.deploy_dir.mkdir()
    (foreign / "lock").write_text("4242 2026-11-20T00:00:00Z\n", encoding="utf-8")
    (ctx.paths.deploy_dir / "lock").symlink_to(foreign / "lock")
    ctx.out = io.StringIO()
    assert tool.main(["last"], ctx) == 1
    assert "4242" not in ctx.out.getvalue() + "".join(capsys.readouterr())


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


#: A186: вызовы, что пишут, читают или обходят файлы, — только в классе `Files`; элемент на
#: каждый (L37).
DISK_CALLS = [
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
    "os.fchmod(a, 0)",
    "os.open(a, os.O_RDONLY)",
    "os.listdir(a)",
    "os.scandir(a)",
    "open(a)",
    "a.read_text()",
    "a.read_bytes()",
    "a.glob('*.log')",
    "a.iterdir()",
]

MODULE_CALLS = {call.split("(")[0] for call in DISK_CALLS if call.startswith(("os.", "shutil."))}

PATH_CALLS = {call.split("(")[0][2:] for call in DISK_CALLS if call.startswith("a.")}


def touches_disk(call: ast.Call) -> bool:
    """A186: функция `os`/`shutil` из списка, `open`, метод пути из списка — не через функцию
    файлов `….files.`."""
    name = ast.unparse(call.func)
    if name in MODULE_CALLS or name == "open":
        return True
    method = call.func.attr if isinstance(call.func, ast.Attribute) else ""
    return method in PATH_CALLS and not name.endswith(f"files.{method}")


def disk_calls_outside_files(source: str) -> list[str]:
    """A186: вызовы, что пишут, читают или обходят файлы, вне класса `Files`."""
    found: list[str] = []

    def visit(node: ast.AST, inside: bool) -> None:
        inside = node.name == "Files" if isinstance(node, ast.ClassDef) else inside
        if isinstance(node, ast.Call) and not inside and touches_disk(node):
            found.append(ast.unparse(node))
        for child in ast.iter_child_nodes(node):
            visit(child, inside)

    visit(ast.parse(source), False)
    return found


@pytest.mark.parametrize("call", DISK_CALLS)
def test_a186_disk_call_outside_the_file_function_is_found(call: str) -> None:
    """A186: проба детектора — вызов, что пишет, читает или обходит файлы, вне `Files` найден,
    внутри — нет."""
    assert disk_calls_outside_files(f"def f(a, b):\n    {call}\n") == [call]
    assert disk_calls_outside_files(f"class Files:\n    def f(a, b):\n        {call}\n") == []


def test_a186_script_touches_files_only_through_the_file_function() -> None:
    """A186: в скрипте нет вызова, что пишет, читает или обходит файлы, вне функции файлов;
    вызовы через `ctx.files` и проверки вида `is_dir` — не находки."""
    assert disk_calls_outside_files(SCRIPT.read_text(encoding="utf-8")) == []
    lawful = "def f(a, ctx):\n    ctx.files.read(a)\n    ctx.files.mkdir(a)\n    a.is_dir()\n"
    assert disk_calls_outside_files(lawful) == []


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
    env = tool.read_env(ctx.files, ctx.paths.env_file)
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
    номером, без текста; нет файла, ссылка вместо файла и не обычный файл — отказ словами."""
    ctx.paths.env_file.write_text(text, encoding="utf-8")
    with pytest.raises(Refused, match=expected) as refused:
        tool.read_env(ctx.files, ctx.paths.env_file)
    assert "secret-without-name" not in str(refused.value)
    ctx.paths.env_file.unlink()
    with pytest.raises(Refused, match="файла окружения нет — сначала env-init"):
        tool.read_env(ctx.files, ctx.paths.env_file)
    ctx.paths.env_file.symlink_to(ctx.paths.service / "other")
    with pytest.raises(Refused, match="ссылка"):
        tool.read_env(ctx.files, ctx.paths.env_file)
    ctx.paths.env_file.unlink()
    ctx.paths.env_file.mkdir()
    with pytest.raises(Refused, match="не обычный файл"):
        tool.read_env(ctx.files, ctx.paths.env_file)


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
        assert ctx.files.lock_state(lock).startswith(f"идёт выкат: процесс {pid} с ")
    finally:
        ctx.files.unlock(fd)
    assert ctx.files.lock_state(lock) == "выката нет"
    lock.write_text("99999999 2026-11-20T00:00:00Z\n", encoding="utf-8")
    assert ctx.files.lock_state(lock).startswith("устаревшая метка: процесс 99999999")
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
