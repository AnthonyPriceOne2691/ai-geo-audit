"""Скрипт выката AI GEO Audit на общий сервер (Ч1.6а).

Запуск — `python3 -I deploy/deploy.py <команда>` от root; только стандартная библиотека. Внешние
команды — одним исполнителем, значения — окружением процесса; на диск — одной функцией файлов под
своими корнями; ход — сначала в журнал `.deploy/`; в выводе и журнале нет секретов, адресов,
портов, домена и имён соседа (A183, A186).
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import hashlib
import http.client
import ipaddress
import json
import os
import pwd
import re
import signal
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, TextIO
from urllib.parse import urlsplit

PROJECT = "ai-geo-audit"
PROJECT_LABEL = "com.docker.compose.project"
PLACEHOLDER = "CHANGE_ME"
TIMEOUT = 30.0
PROBE_TIMEOUT = 5.0
#: PATH дочерних процессов: окружение оператора командам не передаётся.
SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
LAST_LINES = 60
NAME_RE = re.compile(r"[A-Z][A-Z0-9_]*")
LABEL_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?")
ID_RE = re.compile(r"[0-9a-f]{64}")
NO_SUCH_RE = re.compile(r"No such (container|object)")
PS_ALL = ("docker", "ps", "--all", "--quiet", "--no-trunc")
#: Только поля снимка и проверки — окружений контейнеров соседа скрипт не получает. Узлы целиком
#: через `json`: docker 29 исполняет шаблон над картой, и `{{if .State.Health}}` без проверки
#: здоровья падает.
INSPECT_FORMAT = (
    '{"id":{{json .Id}},"name":{{json .Name}},"labels":{{json .Config.Labels}},'
    '"state":{{json .State}},"restarts":{{json .RestartCount}},'
    '"memory":{{json .HostConfig.Memory}},"ports":{{json .HostConfig.PortBindings}}}'
)
INSPECT = ("docker", "container", "inspect", "--format", INSPECT_FORMAT)
SNAPSHOT_FIELDS = ("Id", "StartedAt", "RestartCount", "здоровье")


class RefusedError(Exception):
    """Отказ словами: причина и что делать; в тексте только имена и числа, без значений."""

    reported = False


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Paths:
    """Свои корни на сервере; тесты подставляют временный каталог."""

    service: Path = Path("/srv/ai-geo-audit")
    backups: Path = Path("/srv/ai-geo-audit-backups")
    etc: Path = Path("/etc/ai-geo-audit")

    @property
    def roots(self) -> tuple[Path, ...]:
        return (self.service, self.backups, self.etc)

    @property
    def env_file(self) -> Path:
        return self.service / ".env"

    @property
    def deploy_dir(self) -> Path:
        return self.service / ".deploy"


class Result(NamedTuple):
    code: int
    out: str
    err: str


class Runner:
    """Единственный исполнитель внешних команд: предел времени, пустой stdin, своя сессия — по
    пределу гаснет вся группа; значения — только окружением (`env`), аргументы видны всем.
    Окружение оператора командам не передаётся: HOME — из учётной записи пользователя процесса,
    иначе docker под root читал бы настройки и плагины из чужого `$HOME/.docker`."""

    def run(
        self, argv: Sequence[str], *, timeout: float = TIMEOUT, env: Mapping[str, str] | None = None
    ) -> Result:
        what = " ".join(argv[:2])
        home = pwd.getpwuid(os.geteuid()).pw_dir
        child = {"PATH": SAFE_PATH, "LC_ALL": "C.UTF-8", "HOME": home}
        try:
            proc = subprocess.Popen(  # noqa: S603 — список без шелла, значения окружением (A183)
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child | dict(env or {}),
                start_new_session=True,
            )
        except OSError as exc:
            raise RefusedError(f"{what}: не запустилась ({exc.strerror})") from None
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.communicate(timeout=5)
            raise RefusedError(f"{what}: не уложилась в {timeout:g} с — повторить позже") from None
        return Result(proc.returncode, out.decode(errors="replace"), err.decode(errors="replace"))


class Files:
    """Единственный путь к своим файлам — запись, чтение и обход только под своими корнями и не
    по ссылке. Запись — временным файлом, сразу 600, и переименованием: обрыв прежний файл не
    портит. Операции видны тесту (`ops`)."""

    def __init__(self, roots: Sequence[Path]) -> None:
        self.roots = tuple(Path(os.path.abspath(root)) for root in roots)
        self.ops: list[tuple[str, ...]] = []

    def own(self, path: Path, *, root_itself: bool = False) -> Path:
        """Путь под своим корнем и по написанию, и по настоящему каталогу; ни корень, ни сам путь
        — не ссылка. Иначе отказ: за ссылкой может быть чужое."""
        full = Path(os.path.abspath(path))
        for root in self.roots:
            if full.is_relative_to(root) and (full != root or root_itself):
                if root.is_symlink() or full.is_symlink():
                    link = root if root.is_symlink() else full
                    raise RefusedError(f"{link} — ссылка: скрипт по ссылкам не ходит")
                parent = os.path.realpath(full if full == root else full.parent)
                if Path(parent).is_relative_to(os.path.realpath(root)):
                    return full
        raise RefusedError("путь вне своих корней: " + ", ".join(map(str, self.roots)))

    def write(self, path: Path, data: bytes, *, mode: int = 0o600) -> None:
        target = self.own(path)
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
                os.fsync(out.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, target)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
        self.ops.append(("write", str(target)))

    def _open(self, target: Path, flags: int) -> int:
        """Свой обычный файл, без ссылки и без ожидания: канал не ждёт второго конца, каталог и
        устройство не открываются — не обычный файл: отказ сразу."""
        try:
            fd = os.open(target, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        except OSError as exc:
            if exc.errno not in (errno.ENXIO, errno.EISDIR, errno.ELOOP):
                raise
            raise RefusedError(f"{target.name} — не обычный файл") from None
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise RefusedError(f"{target.name} — не обычный файл")
        os.set_blocking(fd, True)
        return fd

    def append(self, path: Path, text: str) -> None:
        target = self.own(path)
        fd = self._open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        with os.fdopen(fd, "a", encoding="utf-8") as out:
            out.write(text)
        self.ops.append(("append", str(target)))

    def mkdir(self, path: Path, *, mode: int = 0o700) -> None:
        """Свой каталог; права — через дескриптор самого каталога, не по ссылке."""
        target = self.own(path, root_itself=True)
        if not os.path.lexists(target):
            os.mkdir(target, mode)
            self.ops.append(("mkdir", str(target)))
        try:
            fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except NotADirectoryError:
            raise RefusedError(f"{target} — не каталог") from None
        try:
            os.fchmod(fd, mode)
        finally:
            os.close(fd)

    def read(self, path: Path) -> tuple[str, os.stat_result]:
        """Свой файл целиком, не по ссылке, — с правами и владельцем того же открытого файла."""
        fd = self._open(self.own(path), os.O_RDONLY)
        info = os.fstat(fd)
        with os.fdopen(fd, encoding="utf-8", errors="replace") as source:
            return source.read(), info

    def names(self, path: Path) -> list[str]:
        """Имена в своём каталоге, не по ссылке; каталога нет — пусто, не каталог — отказ."""
        try:
            return sorted(os.listdir(self.own(path, root_itself=True)))
        except FileNotFoundError:
            return []
        except NotADirectoryError:
            raise RefusedError(f"{path.name} — не каталог") from None

    def remove(self, path: Path) -> None:
        target = self.own(path)
        os.unlink(target)
        self.ops.append(("remove", str(target)))

    def rename(self, source: Path, target: Path) -> None:
        src, dst = self.own(source), self.own(target)
        os.replace(src, dst)
        self.ops.append(("rename", str(src), str(dst)))

    def lock(self, path: Path) -> tuple[int, str]:
        """Замок одного запуска: flock на время команды и метка «процесс и время». Занят — отказ
        сразу; метка без замка — от мёртвого процесса, она возвращается как устаревшая."""
        target = self.own(path)
        fd = self._open(target, os.O_RDWR | os.O_CREAT)
        mark = os.pread(fd, 200, 0).decode(errors="replace").strip()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise RefusedError(
                f"идёт выкат: {describe(mark)} — дождаться конца; ход — last"
            ) from None
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{os.getpid()} {utc_stamp()}\n".encode(), 0)
        self.ops.append(("lock", str(target)))
        return fd, mark

    def unlock(self, fd: int) -> None:
        os.ftruncate(fd, 0)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def lock_state(self, path: Path) -> str:
        """Идёт ли выкат — по своему замку, не трогая метку."""
        try:
            fd = self._open(self.own(path), os.O_RDONLY)
        except FileNotFoundError:
            return "выката нет"
        try:
            mark = os.pread(fd, 200, 0).decode(errors="replace").strip()
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return f"идёт выкат: {describe(mark)}"
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        return f"устаревшая метка: {describe(mark)} — процесс не живёт" if mark else "выката нет"


def describe(mark: str) -> str:
    pid, _, since = mark.partition(" ")
    return f"процесс {pid} с {since}" if pid.isdigit() and since else "метка без процесса"


class Report:
    """Строка хода — сначала в журнал, потом в терминал, если он ещё есть."""

    def __init__(self, files: Files, log: Path, out: TextIO) -> None:
        self.files, self.log, self.out = files, log, out

    def line(self, text: str) -> None:
        row = f"{utc_stamp()} {text}\n"
        self.files.append(self.log, row)
        with contextlib.suppress(OSError, ValueError):  # обрыв связи шаг не роняет
            self.out.write(row)
            self.out.flush()


def http_code(url: str, timeout: float = PROBE_TIMEOUT) -> int:
    """Код ответа адреса на петле, без перенаправлений; нет ответа — 0."""
    parts = urlsplit(url)
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    conn = http.client.HTTPConnection(parts.hostname or "", parts.port or 80, timeout=timeout)
    try:
        conn.request("GET", target)
        return conn.getresponse().status
    except (OSError, http.client.HTTPException):
        return 0
    finally:
        conn.close()


@dataclass
class Context:
    """С чем работает команда; тесты подставляют корни, исполнителя, пробу и вывод."""

    paths: Paths = field(default_factory=Paths)
    runner: Runner = field(default_factory=Runner)
    probe: Callable[[str], int] = http_code
    out: TextIO = field(default_factory=lambda: sys.stdout)
    owner: int = 0  # владелец файлов сервера — root; в тестах — пользователь прогона
    files: Files = field(init=False)

    def __post_init__(self) -> None:
        self.files = Files(self.paths.roots)


@contextlib.contextmanager
def session(ctx: Context, command: str) -> Iterator[Report]:
    """Запуск команды: журнал хода, замок, первая и последняя строки; отказ — тоже в журнал."""
    if not ctx.paths.service.is_dir():
        raise RefusedError(f"нет каталога {ctx.paths.service} — сначала git clone по памятке")
    ctx.files.mkdir(ctx.paths.deploy_dir)
    log = ctx.paths.deploy_dir / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{command}.log"
    rep = Report(ctx.files, log, ctx.out)
    rep.line(f"{command}: начало")
    fd = -1
    try:
        fd, stale = ctx.files.lock(ctx.paths.deploy_dir / "lock")
        if stale:
            rep.line(f"устаревшая метка: {describe(stale)} — процесс не живёт, метка снята")
        yield rep
    except RefusedError as exc:
        rep.line(f"итог: отказ — {exc}")
        exc.reported = True
        raise
    except Exception as exc:
        rep.line(f"итог: сбой скрипта — {type(exc).__name__}")  # текст ошибки может нести значение
        raise
    else:
        rep.line("итог: ок")
    finally:
        if fd >= 0:
            ctx.files.unlock(fd)


class EnvFile(NamedTuple):
    """Файл окружения сервера, прочитанный без исполнения."""

    values: dict[str, str]
    mode: int
    uid: int


def read_env(files: Files, path: Path) -> EnvFile:
    """Строки «ИМЯ=значение»; пустые и `#` — мимо; ничего не подставляется и не исполняется."""
    try:
        text, info = files.read(path)
    except FileNotFoundError:
        raise RefusedError("файла окружения нет — сначала env-init") from None
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if not sep or not NAME_RE.fullmatch(name):
            raise RefusedError(f"файл окружения, строка {number}: не «ИМЯ=значение»")
        if name in values:
            raise RefusedError(f"файл окружения: имя {name} дважды")
        values[name] = value
    return EnvFile(values, stat.S_IMODE(info.st_mode), info.st_uid)


def env_problems(env: EnvFile, *, owner: int, may_be_empty: frozenset[str]) -> list[str]:
    """Права, владелец, заглушки и пустые значения — словами, без значений."""
    problems = ["права файла окружения шире 600 — chmod 600"] if env.mode & 0o077 else []
    problems += ["владелец файла окружения не root — chown root"] if env.uid != owner else []
    for name, value in env.values.items():
        if value == PLACEHOLDER:
            problems.append(f"{name}: заглушка {PLACEHOLDER}")
        elif not value and name not in may_be_empty:
            problems.append(f"{name}: пусто")
    return problems


def site_name(domain: str) -> str:
    """Имя сайта — `ai-geo-audit.<SITE_DOMAIN>`; негодное — отказ словами без значения (A184)."""
    if not domain:
        raise RefusedError("SITE_DOMAIN: пусто — задаёт владелец, put-secret")
    if "*" in domain:
        raise RefusedError("SITE_DOMAIN: звёздочка не допускается — нужен один домен, без маски")
    if domain.endswith("."):
        raise RefusedError("SITE_DOMAIN: точка в конце не допускается")
    if re.search(r"[/\s]", domain):
        raise RefusedError("SITE_DOMAIN: «/» и пробелы не допускаются — только имя домена")
    name = f"{PROJECT}.{domain}".lower()
    if len(name) > 253:
        raise RefusedError("имя сайта длиннее 253 знаков")
    if any(len(label) > 63 for label in name.split(".")):
        raise RefusedError("метка имени сайта длиннее 63 знаков")
    if not all(LABEL_RE.fullmatch(label) for label in name.split(".")):
        raise RefusedError("SITE_DOMAIN: метки — только латинские буквы, цифры и дефис")
    return name


def server_env(ctx: Context, *, may_be_empty: frozenset[str] = frozenset()) -> EnvFile:
    """Файл сервера и имя сайта — до любой внешней команды (A184, A185)."""
    env = read_env(ctx.files, ctx.paths.env_file)
    problems = env_problems(env, owner=ctx.owner, may_be_empty=may_be_empty)
    if problems:
        raise RefusedError("файл окружения: " + "; ".join(problems))
    site_name(env.values.get("SITE_DOMAIN", ""))
    return env


class Container(NamedTuple):
    """Контейнер по `docker container inspect`: только поля снимка и проверки."""

    id: str
    name: str
    project: str
    started: str
    restarts: int
    health: str
    memory: int
    ports: dict[str, object]


def parse_container(line: str) -> Container:
    raw = json.loads(line)
    state = raw["state"]
    return Container(
        raw["id"],
        raw["name"].lstrip("/"),
        (raw["labels"] or {}).get(PROJECT_LABEL, ""),
        state["StartedAt"],
        int(raw["restarts"]),
        # Без проверки здоровья — состояние: упавший контейнер виден и так.
        (state.get("Health") or {}).get("Status") or state["Status"],
        int(raw["memory"] or 0),
        raw["ports"] or {},
    )


def containers(runner: Runner) -> list[Container]:
    """Все контейнеры машины, и остановленные: `docker ps`, затем `docker container inspect`."""
    listing = runner.run(PS_ALL)
    if listing.code:
        raise RefusedError(f"docker ps: код {listing.code} — запустить руками и прочесть ошибку")
    ids = listing.out.split()
    if not all(ID_RE.fullmatch(i) for i in ids):
        raise RefusedError("ответ docker ps не разобран — сверить версию docker с записями тестов")
    if not ids:
        return []
    found = runner.run((*INSPECT, *ids))
    errors = [line for line in found.err.splitlines() if line.strip()]
    # Контейнер, удалённый между двумя командами, — «пропал», а не сбой снимка.
    if found.code and not (errors and all(NO_SUCH_RE.search(e) for e in errors)):
        raise RefusedError(f"docker container inspect: код {found.code} — запустить руками")
    try:
        return [parse_container(line) for line in found.out.splitlines() if line.strip()]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise RefusedError(
            "ответ docker container inspect не разобран — сверить версию docker с записями тестов"
        ) from None


def health_urls(raw: str) -> list[str]:
    """Адреса проверки соседа: только http на петле, иначе отказ — до любого запроса."""
    urls = [url.strip() for url in raw.split(",") if url.strip()]
    for number, url in enumerate(urls, 1):
        parts = urlsplit(url)
        try:
            loopback = ipaddress.ip_address(parts.hostname or "").is_loopback
            port = parts.port
        except ValueError:
            loopback, port = False, None
        if parts.scheme != "http" or not loopback or parts.username or port == 0:
            raise RefusedError(
                f"NEIGHBOR_HEALTH_URLS: адрес № {number} не на петле — нужен "
                "http://127.0.0.1:<порт>/<путь>"
            )
    return urls


class Snapshot(NamedTuple):
    """Снимок соседа: контейнеры не нашего проекта по sha256 имени — имён в снимке нет — и коды
    адресов проверки."""

    seen: dict[str, tuple[str, str, int, str]]
    codes: tuple[int, ...]


def neighbor_snapshot(ctx: Context, raw_urls: str) -> Snapshot:
    """Снимок соседа (A186): адреса судятся до любого запроса; свой стек не входит."""
    urls = health_urls(raw_urls)
    seen = {
        hashlib.sha256(c.name.encode()).hexdigest()[:16]: (c.id, c.started, c.restarts, c.health)
        for c in containers(ctx.runner)
        if c.project != PROJECT
    }
    return Snapshot(seen, tuple(ctx.probe(url) for url in urls))


class Comparison(NamedTuple):
    line: str
    red: bool


def compare(before: Snapshot, after: Snapshot) -> Comparison:
    """Снимки до и после — числами, без имён (A186): расхождение у общих и смена кодов — красное,
    пропавшие и новые — строкой."""
    common = before.seen.keys() & after.seen.keys()
    changed = [sum(before.seen[k][i] != after.seen[k][i] for k in common) for i in range(4)]
    restarted = sum(before.seen[k][:3] != after.seen[k][:3] for k in common)
    codes = sum(a != b for a, b in zip(before.codes, after.codes, strict=False))
    codes += abs(len(before.codes) - len(after.codes))
    gone, new = (
        len(before.seen.keys() - after.seen.keys()),
        len(after.seen.keys() - before.seen.keys()),
    )
    red = [f"{name} {n}" for name, n in zip(SNAPSHOT_FIELDS, changed, strict=True) if n]
    red += [f"коды адресов {codes}"] if codes else []
    line = (
        f"снимок соседа: общих {len(common)}, перезапусков {restarted}, "
        f"{f'кодов изменилось {codes}' if codes else 'коды прежние'}; пропало {gone}, новых {new}"
    )
    return Comparison(line + (" — красное: " + ", ".join(red) if red else ""), bool(red))


def cmd_last(ctx: Context, _args: argparse.Namespace) -> int:
    """Последний журнал хода и идёт ли выкат — функцией файлов; журнала не пишет, замка не
    берёт."""
    logs = [name for name in ctx.files.names(ctx.paths.deploy_dir) if name.endswith(".log")]
    if logs:
        lines = ctx.files.read(ctx.paths.deploy_dir / logs[-1])[0].splitlines()
        shown = lines[-LAST_LINES:]
        print(f"журнал {logs[-1]}: строк {len(lines)}, показано {len(shown)}", file=ctx.out)
        print(*shown, sep="\n", file=ctx.out)
    else:
        print("журналов хода нет", file=ctx.out)
    print(ctx.files.lock_state(ctx.paths.deploy_dir / "lock"), file=ctx.out)
    return 0


COMMANDS: dict[str, tuple[Callable[[Context, argparse.Namespace], int], str]] = {
    "last": (cmd_last, "последний журнал хода и идёт ли выкат"),
}


def main(argv: Sequence[str] | None = None, ctx: Context | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deploy.py", description="Выкат AI GEO Audit")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, (_command, summary) in COMMANDS.items():
        commands.add_parser(name, help=summary)
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command][0](ctx or Context(), args)
    except RefusedError as exc:
        if not exc.reported:
            with contextlib.suppress(OSError, ValueError):
                print(f"отказ: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # текст чужой ошибки может нести значение (A183): только её вид
        with contextlib.suppress(OSError, ValueError):
            print(f"сбой скрипта: {type(exc).__name__}; ход — last", file=sys.stderr)
        return 1


def entry(argv: Sequence[str] | None = None, ctx: Context | None = None) -> int:
    """Вход с терминала: обрыв связи (SIGHUP) шаг не роняет."""
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    return main(argv, ctx)


if __name__ == "__main__":
    sys.exit(entry())
