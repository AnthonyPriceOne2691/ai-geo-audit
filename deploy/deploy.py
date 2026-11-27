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
import secrets
import signal
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, TextIO
from urllib.parse import unquote, urlsplit

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
#: Блок «сервер» `.env.example` (A182): строки `# ИМЯ=…` под строкой `## <пометка>` — кто задаёт.
BY_SCRIPT, BY_OWNER, BY_DEPLOY = "создаёт скрипт", "задаёт владелец", "пишет выкат"
MARK_RE = re.compile(f"## ({BY_SCRIPT}|{BY_OWNER}|{BY_DEPLOY})")
SAMPLE_RE = re.compile(r"# ([A-Z][A-Z0-9_]*)=(.*)")
#: «Создаёт скрипт»: пароли — 48 знаков hex, секрет подписи — 64, путь мастер-ключа — свой корень;
#: адрес — строка образца с паролем своей пары вместо заглушки.
PASSWORDS = ("DB_PASSWORD", "POSTGRES_MIGRATE_PASSWORD", "POSTGRES_APP_PASSWORD", "REDIS_PASSWORD")
SIGNING = "JWT_SECRET"
KEY_PATH = "KEYS_MASTER_KEY_HOST_FILE"
PAIRS = {
    "MIGRATE_DATABASE_URL": "POSTGRES_MIGRATE_PASSWORD",
    "API_DATABASE_URL": "POSTGRES_APP_PASSWORD",
    "API_REDIS_URL": "REDIS_PASSWORD",
}
#: Владелец мастер-ключа — пользователь процесса api в образе.
API_UID = 10001


class RefusedError(Exception):
    """Отказ словами: причина и что делать; в тексте только имена и числа, без значений."""

    reported = False


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Paths:
    """Свои корни на сервере; тесты подставляют временный каталог. `neighbors` — не свой корень:
    там проекты соседа, его файлы compose `deploy.py check` читает на глубине 2."""

    service: Path = Path("/srv/ai-geo-audit")
    backups: Path = Path("/srv/ai-geo-audit-backups")
    etc: Path = Path("/etc/ai-geo-audit")
    neighbors: Path = Path("/srv")

    @property
    def roots(self) -> tuple[Path, ...]:
        return (self.service, self.backups, self.etc)

    @property
    def env_file(self) -> Path:
        return self.service / ".env"

    @property
    def deploy_dir(self) -> Path:
        return self.service / ".deploy"

    @property
    def sample(self) -> Path:
        return self.service / ".env.example"

    @property
    def master_key(self) -> Path:
        return self.etc / "master.key"

    @property
    def incoming(self) -> Path:
        return self.etc / "incoming"


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

    def exists(self, path: Path) -> bool:
        """Есть ли свой путь — и ссылка считается."""
        return os.path.lexists(self.own(path))

    def chown(self, path: Path, uid: int) -> None:
        """Владелец своего файла — не по ссылке."""
        target = self.own(path)
        os.chown(target, uid, -1, follow_symlinks=False)
        self.ops.append(("chown", str(target)))

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


def free_space(path: Path) -> int:
    """Место для записи (`f_bavail`), байт; пути ещё нет — корень копий до первой копии — у
    ближайшего каталога выше."""
    where = next(place for place in (path, *path.parents) if place.is_dir())
    info = os.statvfs(where)
    return info.f_bavail * info.f_frsize


@dataclass
class Context:
    """С чем работает команда; тесты подставляют корни, исполнителя, пробу, вывод и место."""

    paths: Paths = field(default_factory=Paths)
    runner: Runner = field(default_factory=Runner)
    probe: Callable[[str], int] = http_code
    out: TextIO = field(default_factory=lambda: sys.stdout)
    owner: int = 0  # владелец файлов сервера — root; в тестах — пользователь прогона
    key_owner: int = API_UID  # владелец мастер-ключа; в тестах — пользователь прогона
    space: Callable[[Path], int] = free_space
    files: Files = field(init=False)

    def __post_init__(self) -> None:
        self.files = Files(self.paths.roots)


@contextlib.contextmanager
def session(ctx: Context, command: str) -> Iterator[Report]:
    """Запуск команды: журнал хода, замок, первая и последняя строки; отказ — тоже в журнал."""
    if not ctx.paths.service.is_dir():
        raise RefusedError(f"нет каталога {ctx.paths.service} — сначала git clone по памятке")
    ctx.files.mkdir(ctx.paths.deploy_dir)
    log = ctx.paths.deploy_dir / f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{command}.log"
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


def parse_env(text: str, what: str) -> dict[str, str]:
    """Строки «ИМЯ=значение»; пустые и `#` — мимо; ничего не подставляется и не исполняется."""
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if not sep or not NAME_RE.fullmatch(name):
            raise RefusedError(f"{what}, строка {number}: не «ИМЯ=значение»")
        if name in values:
            raise RefusedError(f"{what}: имя {name} дважды")
        values[name] = value
    return values


def read_env(files: Files, path: Path) -> EnvFile:
    """Файл окружения сервера с правами и владельцем; нет файла — отказ «сначала env-init»."""
    try:
        text, info = files.read(path)
    except FileNotFoundError:
        raise RefusedError("файла окружения нет — сначала env-init") from None
    return EnvFile(parse_env(text, "файл окружения"), stat.S_IMODE(info.st_mode), info.st_uid)


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
    running: bool


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
        bool(state["Running"]),
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
    """Адреса проверки соседа: только http на петле и без учётных данных, иначе отказ — до любого
    запроса; кривая запись адреса — тот же отказ, а не сбой разбора."""
    urls = [url.strip() for url in raw.split(",") if url.strip()]
    for number, url in enumerate(urls, 1):
        try:
            parts = urlsplit(url)
            good = (
                parts.scheme == "http"
                and ipaddress.ip_address(parts.hostname or "").is_loopback
                # Любая часть учётных данных — `@` в адресе: и пустое имя (`:p@`, `@`, `:@`).
                and parts.username is None
                and parts.port != 0
            )
        except ValueError:  # запись адреса, порта или IP не разбирается
            good = False
        if not good:
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


def server_block(files: Files, paths: Paths) -> dict[str, tuple[str, str]]:
    """Блок «сервер» образца клона: имя → пометка и значение образца (A182)."""
    try:
        text = files.read(paths.sample)[0]
    except FileNotFoundError:
        raise RefusedError("образца .env.example нет — нужен клон репозитория") from None
    block: dict[str, tuple[str, str]] = {}
    mark = None
    for line in text.splitlines():
        if found := MARK_RE.fullmatch(line):
            mark = found[1]
        elif mark and (pair := SAMPLE_RE.fullmatch(line)):
            block[pair[1]] = (mark, pair[2])
        elif mark and not line.startswith("#"):
            mark = None
    if not block:
        raise RefusedError("в образце нет блока «сервер» — сверить клон с репозиторием")
    return block


def marked(block: Mapping[str, tuple[str, str]], mark: str) -> list[str]:
    return [name for name, (have, _) in block.items() if have == mark]


def env_values(block: Mapping[str, tuple[str, str]], paths: Paths) -> dict[str, str]:
    """Значения файла сервера по пометкам: «создаёт скрипт» — случайные и адреса с паролем своей
    пары; «задаёт владелец» — заглушка; «пишет выкат» — пусто (A182)."""
    made: dict[str, str] = {}
    for name in marked(block, BY_SCRIPT):
        if name in PASSWORDS:
            made[name] = secrets.token_hex(24)
        elif name == SIGNING:
            made[name] = secrets.token_hex(32)
        elif name == KEY_PATH:
            made[name] = str(paths.master_key)
        elif name not in PAIRS:
            raise RefusedError(f"{name}: «{BY_SCRIPT}», а скрипт не знает, как его сделать")
    for name, password in PAIRS.items():
        mark, template = block.get(name, ("", ""))
        if mark != BY_SCRIPT or password not in made or PLACEHOLDER not in template:
            raise RefusedError(f"{name}: в образце нет адреса с заглушкой пароля {password}")
        made[name] = template.replace(PLACEHOLDER, made[password])
    fixed = {BY_OWNER: PLACEHOLDER, BY_DEPLOY: ""}
    return {name: made.get(name, fixed.get(mark, "")) for name, (mark, _) in block.items()}


def cmd_env_init(ctx: Context, _args: argparse.Namespace) -> int:
    """Файл окружения сервера, мастер-ключ и каталог входящих (A182). Второй запуск — отказ: база
    помнит пароли первого, новый секрет подписи гасит все пропуска."""
    with session(ctx, "env-init") as rep:
        paths = ctx.paths
        if ctx.files.exists(paths.env_file):
            raise RefusedError("файл окружения уже есть — значения не пересоздаются")
        if ctx.files.exists(paths.master_key):
            raise RefusedError(
                "мастер-ключ уже есть, файла окружения нет — значения не пересоздаются: ключами "
                "сервисов в базе владеет этот ключ; разобрать по памятке"
            )
        block = server_block(ctx.files, paths)
        values = env_values(block, paths)
        ctx.files.mkdir(paths.etc)
        ctx.files.mkdir(paths.incoming)
        ctx.files.write(paths.master_key, f"{secrets.token_hex(32)}\n".encode(), mode=0o400)
        ctx.files.chown(paths.master_key, ctx.key_owner)
        ctx.files.write(paths.env_file, "".join(f"{k}={v}\n" for k, v in values.items()).encode())
        rep.line(f"создано: {len(marked(block, BY_SCRIPT))}; мастер-ключ и каталог входящих есть")
        rep.line(f"{BY_OWNER} — put-secret: {', '.join(marked(block, BY_OWNER))}")
        rep.line(f"{BY_DEPLOY}: {', '.join(marked(block, BY_DEPLOY))}")
    return 0


def cmd_put_secret(ctx: Context, args: argparse.Namespace) -> int:
    """Секреты владельца файлом (A182): только имена «задаёт владелец», замена своих строк файла
    сервера, права 600, входящий удаляется. Отказ — ни файл сервера, ни входящий не тронуты."""
    with session(ctx, "put-secret") as rep:
        source = Path(os.path.abspath(args.file))
        if source.parent != ctx.paths.incoming:
            raise RefusedError("входящий файл — только из каталога входящих")
        try:
            text, info = ctx.files.read(source)
        except FileNotFoundError:
            raise RefusedError("входящего файла нет — положить его в каталог входящих") from None
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise RefusedError("права входящего файла шире 600 — chmod 600 и снова")
        given = parse_env(text, "входящий файл")
        owner = marked(server_block(ctx.files, ctx.paths), BY_OWNER)
        if foreign := [name for name in given if name not in owner]:
            raise RefusedError(f"{', '.join(foreign)} — не «{BY_OWNER}»: put-secret их не вносит")
        if not given:
            raise RefusedError("входящий файл пуст — нечего вносить")
        try:
            current = ctx.files.read(ctx.paths.env_file)[0]
        except FileNotFoundError:
            raise RefusedError("файла окружения нет — сначала env-init") from None
        if missing := [name for name in given if name not in parse_env(current, "файл окружения")]:
            raise RefusedError(f"в файле окружения нет имён: {', '.join(missing)} — env-init")
        rows = current.splitlines(keepends=True)
        new = [
            f"{n}={given[n]}\n" if (n := row.partition("=")[0]) in given else row for row in rows
        ]
        ctx.files.write(ctx.paths.env_file, "".join(new).encode())
        ctx.files.remove(source)
        rep.line(f"внесено: {len(given)} — {', '.join(given)}")
    return 0


#: `deploy.py check` (A185): памяти после наших пределов — не меньше 10 ГБ (8 соседу и 2 запаса),
#: места — 20 ГБ (срез 0); образы — по дайджесту (A181, третья строка).
GB, MEMORY_FLOOR, DISK_FLOOR = 1 << 30, 10, 20
IMAGES = ("BACKEND_IMAGE", "WEB_IMAGE")
IMAGE_RE = re.compile(r"[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}")
PORT_RE, LIMIT_RE = re.compile(r"[0-9]{1,5}"), re.compile(r"([0-9]+)([kmg]?)b?")
#: Переменная compose: `${VAR}`, `${VAR:-умолчание}`, `${VAR-умолчание}`, `${VAR:?…}`, `$VAR`.
VARIABLE_RE = re.compile(r"\$\{(\w+)(?:(:?-)([^}]*)|[^}]*)\}|\$(\w+)")
KEY_RE = re.compile(r"([a-z_]+):\s*(.*)")
ROUTE_TYPES = frozenset({"unreachable", "prohibit", "blackhole", "throw"})
NETWORK_FORMAT = (
    '{"id":{{json .Id}},"name":{{json .Name}},"labels":{{json .Labels}},'
    '"ipam":{{json .IPAM.Config}}}'
)
VOLUME_FORMAT = '{"name":{{json .Name}},"project":{{json (.Label "com.docker.compose.project")}}}'
#: Строки портов и подсетей файлов compose соседа — разбор разведки, блок 12: комментарии мимо;
#: `ports: [..]` строкой, строки блока `ports:` глубже его отступа, строки `subnet`, `ip_range`,
#: `gateway`; вывод — «путь: строка».
NEIGHBOR_AWK = (
    "FNR == 1 { p = 0 } /^[[:space:]]*#/ { next } "
    '/^[[:space:]]*ports:[[:space:]]*\\[/ { print FILENAME ": " $0; next } '
    "/^[[:space:]]*ports:[[:space:]]*(#.*)?$/ "
    "{ p = 1; match($0, /^[[:space:]]*/); ind = RLENGTH; next } "
    "p && /^[[:space:]]*$/ { next } "
    "p { match($0, /^[[:space:]]*/); if (RLENGTH <= ind) p = 0; "
    'else { print FILENAME ": " $0; next } } '
    '/(subnet|ip_range|gateway):/ { print FILENAME ": " $0 }'
)
type IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
#: Строка `deploy.py check` — тема, текст, отказ ли; сеть докера — имя, проект compose, подсети;
#: объявленное соседом — порты хоста, подсети и что «сверить»; пределы памяти — по сервису.
type Line = tuple[str, str, bool]
type Network = tuple[str, str, list[IPNetwork]]
type Declared = tuple[set[int], list[IPNetwork], set[str]]
type Limits = dict[str, int | None]
type Env = Mapping[str, str]


def verdict(topic: str, reasons: Sequence[str], fine: str = "ок") -> Line:
    """«Ок» или названное исключение; есть причины — отказ с ними."""
    return (topic, "отказ — " + "; ".join(reasons), True) if reasons else (topic, fine, False)


def file_verdicts(ctx: Context, env: EnvFile) -> list[Line]:
    """Файл сервера (A185), образы (A181, третья строка), пары «пароль — адрес» (A189, вторая:
    после раскодирования `%XX`; адрес, что разборщики читают по-разному, судит старт, L32)."""
    values, pairs = env.values, []
    for url, password in PAIRS.items():
        try:
            given = unquote(urlsplit(values.get(url, "")).password or "")
        except ValueError:
            pairs.append(f"{url}: адрес не разобран")
            continue
        if given != values.get(password, ""):
            pairs.append(f"пароль в {url} не равен {password}")
    images = {name: values.get(name, "") for name in IMAGES}
    bad = [f"{k}: не <путь>@sha256:<64 знака>" for k in IMAGES if not IMAGE_RE.fullmatch(images[k])]
    first = ("образы", "образов ещё нет — первый выкат", False)
    problems = env_problems(env, owner=ctx.owner, may_be_empty=frozenset(IMAGES))
    lines = [verdict("файл окружения", problems)]
    lines.append(verdict("образы", bad) if any(images.values()) else first)
    return [*lines, verdict("пары «пароль — адрес»", pairs)]


def limit_bytes(raw: object) -> int | None:
    """Предел памяти compose, байт: число или с единицей `b`, `k`, `m`, `g` (`kb`, `mb`, `gb`)."""
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    found = LIMIT_RE.fullmatch(raw.strip().lower()) if isinstance(raw, str) else None
    return int(found[1]) << {"": 0, "k": 10, "m": 20, "g": 30}[found[2]] if found else None


def parsed(text: str) -> Any:
    with contextlib.suppress(ValueError):
        return json.loads(text)
    return None


def docker_json(runner: Runner, argv: Sequence[str], what: str) -> list[dict[str, Any]]:
    """JSON ответа: весь вывод — один объект или строка на объект; код не 0 или не объект —
    отказ словами."""
    done = runner.run(argv)
    if done.code:
        raise RefusedError(f"{what}: код {done.code} — запустить руками")
    whole = parsed(done.out)
    rows = [whole] if isinstance(whole, dict) else [parsed(r) for r in done.out.splitlines() if r]
    if not all(isinstance(row, dict) for row in rows):
        raise RefusedError(f"ответ {what} не разобран — сверить версию docker с записями тестов")
    return rows


def our_stack(ctx: Context) -> tuple[re.Pattern[str], set[str], Limits]:
    """Наш стек по `docker compose config` своих файлов: имена контейнеров, сетей и томов и
    пределы памяти сервисов (не читается — `None`)."""
    files = [("--file", str(ctx.paths.service / f"docker-compose{k}.yml")) for k in ("", ".prod")]
    argv = ("docker", "compose", "--project-name", PROJECT, *(a for f in files for a in f))
    what = "docker compose config"
    rows = docker_json(ctx.runner, (*argv, "config", "--no-interpolate", "--format", "json"), what)
    try:
        services: dict[str, Any] = dict(rows[0]["services"])
        kinds = [(k, v or {}) for kind in ("networks", "volumes") for k, v in rows[0][kind].items()]
        names = {item.get("name") or f"{PROJECT}_{key}" for key, item in kinds}
        memory = {name: limit_bytes(item.get("mem_limit")) for name, item in services.items()}
    except (IndexError, KeyError, TypeError, AttributeError):
        raise RefusedError(f"ответ {what} не разобран — сверить с записями тестов") from None
    return re.compile(f"{PROJECT}-({'|'.join(map(re.escape, services))})-[0-9]+"), names, memory


def docker_networks(runner: Runner) -> list[Network]:
    """Сети докера: `ls` по Id, затем `inspect` — имя, проект compose, подсети."""
    listing = runner.run(("docker", "network", "ls", "--quiet", "--no-trunc"))
    if listing.code:
        raise RefusedError(f"docker network ls: код {listing.code} — запустить руками")
    ids = listing.out.split()
    if not all(ID_RE.fullmatch(i) for i in ids):
        raise RefusedError("ответ docker network ls не разобран — сверить с записями тестов")
    found = ("docker", "network", "inspect", "--format", NETWORK_FORMAT, *ids)
    nets: list[Network] = []
    try:
        for row in docker_json(runner, found, "docker network inspect") if ids else []:
            subnets = [ipaddress.ip_network(entry["Subnet"]) for entry in row["ipam"] or ()]
            nets.append((row["name"], (row["labels"] or {}).get(PROJECT_LABEL, ""), subnets))
    except (KeyError, TypeError, AttributeError, ValueError):
        raise RefusedError("ответ docker network inspect не разобран — сверить версию") from None
    return nets


def host_ports(text: str) -> set[int] | None:
    """Порты хоста: число или диапазон `a-b`; пусто — нет; иначе `None` — «сверить»."""
    first, _, last = text.strip().strip("\"'").partition("-")
    if PORT_RE.fullmatch(first) and PORT_RE.fullmatch(last or first):
        return set(range(int(first), int(last or first) + 1))
    return None if first or last else set()


def line_facts(line: str) -> tuple[list[set[int] | None], list[str]]:
    """Строка файла compose соседа: порты хоста (`None` — «сверить») и записи подсетей.
    Переменная — её умолчание, без умолчания — «?»: значение в файле окружения соседа, его
    скрипт не читает. Короткая запись `[адрес:]порт_хоста:порт_контейнера[/протокол]` — справа."""
    item = line.split(" #", 1)[0].strip().removeprefix("-").strip()
    item = VARIABLE_RE.sub(lambda found: "?" if found[2] is None else found[3], item)
    name, value = key.groups() if (key := KEY_RE.fullmatch(item)) else ("", item)
    if name in {"subnet", "ip_range", "gateway"}:
        return [], [value.strip("\"'")]
    if name == "published":
        return [host_ports(value)], []
    specs = value.strip("[] ").split(",") if name == "ports" else [] if name else [item]
    parts = [spec.strip().strip("\"'").partition("/")[0].rsplit(":", 2) for spec in specs]
    return [host_ports(part[-2] if len(part) > 1 else "") for part in parts if part != [""]], []


def neighbor_files(ctx: Context) -> Declared:
    """Порты хоста и подсети файлов compose соседа на глубине 2 — разбор разведки, блок 12; свои
    корни мимо; что задано переменной без умолчания или не читается — «сверить»."""
    names = ("(", "-name", "docker-compose*.y*ml", "-o", "-name", "compose*.y*ml", ")")
    where = ("find", str(ctx.paths.neighbors), "-mindepth", "2", "-maxdepth", "2", *names)
    done = ctx.runner.run((*where, "-exec", "awk", NEIGHBOR_AWK, "{}", "+"))
    if done.code:
        raise RefusedError(f"файлы compose соседа: код {done.code} — запустить руками")
    ports, subnets, unknown = set[int](), list[IPNetwork](), set[str]()
    for path, sep, line in (row.partition(": ") for row in done.out.splitlines()):
        if not sep:
            raise RefusedError("ответ разбора файлов compose соседа не разобран — запустить руками")
        own = any(Path(path).is_relative_to(root) for root in ctx.paths.roots)
        found, nets = ([], []) if own else line_facts(line)
        unknown |= {"порт"} if None in found else set()
        ports.update(port for each in found if each for port in each)
        for net in nets:
            try:
                subnets.append(ipaddress.ip_network(net, strict=False))
            except ValueError:
                unknown.add("подсеть")
    return ports, subnets, unknown


def listening(runner: Runner) -> set[int]:
    """Порты, что слушают на машине (`ss -ltnH`): адрес — четвёртое поле, ширина плавает."""
    done = runner.run(("ss", "-ltnH"))
    if done.code:
        raise RefusedError(f"ss: код {done.code} — запустить руками")
    rows = [row.split() for row in done.out.splitlines() if row.strip()]
    ports = [row[3].rpartition(":")[2] if len(row) > 3 else "" for row in rows]
    if not all(PORT_RE.fullmatch(port) for port in ports):
        raise RefusedError("ответ ss не разобран — сверить с записями тестов")
    return {int(port) for port in ports}


def host_routes(runner: Runner) -> list[IPNetwork]:
    """Маршруты хоста (`ip route show`): назначение — первое слово, после типа — второе."""
    done = runner.run(("ip", "route", "show"))
    if done.code:
        raise RefusedError(f"ip route: код {done.code} — запустить руками")
    rows = [row.split() for row in done.out.splitlines() if row.strip()]
    targets = [row[1] if row[0] in ROUTE_TYPES and len(row) > 1 else row[0] for row in rows]
    try:
        return [ipaddress.ip_network(t, strict=False) for t in targets if t != "default"]
    except ValueError:
        raise RefusedError("ответ ip route не разобран — запустить руками") from None


def bound_ports(container: Container) -> set[int]:
    """Порты хоста, что контейнер публикует (`HostConfig.PortBindings`), — и остановленный."""
    found = [b for v in container.ports.values() if isinstance(v, list) for b in v]
    hosts = [str(b.get("HostPort") or "") for b in found if isinstance(b, dict)]
    return {port for host in hosts for port in host_ports(host) or ()}


def port_verdicts(ctx: Context, raw: str, found: list[Container], declared: Declared) -> list[Line]:
    """`WEB_PORT` (A185, третья строка): живой — `ss` и привязки чужих контейнеров, свой web не
    помеха; объявленный — файлы compose соседа; переменная без умолчания — «сверить»."""
    if not PORT_RE.fullmatch(raw) or not 0 < int(raw) < 65536:
        return [verdict("порт", ["WEB_PORT: не число от 1 до 65535"])]
    port, live = int(raw), listening(ctx.runner)
    ours = {p for c in found if c.project == PROJECT for p in bound_ports(c)}
    foreign = {p for c in found if c.project != PROJECT for p in bound_ports(c)}
    reasons = ["WEB_PORT занят"] if port in foreign or port in live - ours else []
    reasons += ["WEB_PORT объявлен у соседа"] if port in declared[0] else []
    lines = [verdict("порт", reasons)]
    if "порт" in declared[2]:
        lines.append(("порт", "у соседа порт задан переменной — сверить", False))
    return lines


def subnet_verdicts(ctx: Context, values: Env, nets: list[Network], seen: Declared) -> list[Line]:
    """`FRONT_SUBNET` и `WEB_ADDRESS` (A185, четвёртая строка): подсеть — против чужих сетей
    докера, файлов соседа и маршрутов хоста; своя сеть и её маршрут не помеха."""
    try:
        front = ipaddress.ip_network(values.get("FRONT_SUBNET", ""))
    except ValueError:
        front = None
    if not isinstance(front, ipaddress.IPv4Network):
        return [verdict("подсеть", ["FRONT_SUBNET: не подсеть IPv4 вида a.b.c.d/N"])]
    try:
        inside = ipaddress.ip_address(values.get("WEB_ADDRESS", "")) in front
        reasons = [] if inside else ["WEB_ADDRESS вне FRONT_SUBNET"]
    except ValueError:
        reasons = ["WEB_ADDRESS: не адрес IPv4"]
    ours = [sub for _, project, subs in nets if project == PROJECT for sub in subs]
    sources = {
        "сетью докера": [sub for _, project, subs in nets if project != PROJECT for sub in subs],
        "подсетью из файла compose соседа": seen[1],
        "маршрутом хоста": [route for route in host_routes(ctx.runner) if route not in ours],
    }
    for name, subs in sources.items():
        if any(isinstance(sub, ipaddress.IPv4Network) and front.overlaps(sub) for sub in subs):
            reasons.append(f"FRONT_SUBNET пересекается с {name}")
    lines = [verdict("подсеть", reasons)]
    if "подсеть" in seen[2]:
        lines.append(("подсеть", "у соседа подсеть задана переменной — сверить", False))
    return lines


def resources_verdict(ctx: Context, limits: Limits, found: list[Container]) -> Line:
    """Память и место (A185, пятая строка), числа — в ГБ: `MemAvailable` после наших пределов —
    на первом выкате всех, дальше разницы с запущенным своим стеком — не меньше 10; места в
    каталоге докера и в корне копий — не меньше 20."""
    if unread := sorted(name for name, size in limits.items() if size is None):
        why = f"предел памяти не разобран: {', '.join(unread)} — сверить наложение"
        return verdict("ресурсы", [why])
    meminfo = ctx.runner.run(("cat", "/proc/meminfo"))
    free = re.search(r"^MemAvailable:\s+([0-9]+) kB$", meminfo.out, re.MULTILINE)
    if meminfo.code or not free:
        raise RefusedError("память: MemAvailable не прочитан из /proc/meminfo — запустить руками")
    up = sum(c.memory for c in found if c.project == PROJECT and c.running)
    left = ((int(free[1]) << 10) - max(sum(s or 0 for s in limits.values()) - up, 0)) // GB
    root = ctx.runner.run(("docker", "info", "--format", "{{.DockerRootDir}}"))
    if root.code or not root.out.strip().startswith("/"):
        raise RefusedError("docker info: каталог докера не прочитан — запустить руками")
    docker, backups = (ctx.space(p) // GB for p in (Path(root.out.strip()), ctx.paths.backups))
    short = f"памяти после наших пределов {left} ГБ, нужно не меньше 10: 8 соседу и 2 запаса"
    reasons = [short] if left < MEMORY_FLOOR else []
    for name, size in (("в каталоге докера", docker), ("в корне копий", backups)):
        if size < DISK_FLOOR:
            reasons.append(f"места {name} {size} ГБ, нужно не меньше {DISK_FLOOR}")
    fine = f"ок — памяти после наших пределов {left} ГБ, места: каталог докера {docker} ГБ, "
    return verdict("ресурсы", reasons, fine + f"корень копий {backups} ГБ")


def machine_verdicts(ctx: Context, values: Env) -> list[Line]:
    """Имена (A185, вторая строка: наше имя под чужой меткой проекта или без неё — числом, без
    имён; стек с нашей приставкой под другим проектом — имена не наши), порт, подсеть и ресурсы
    — против живой машины и файлов compose соседа."""
    pattern, names, limits = our_stack(ctx)
    found, nets, declared = containers(ctx.runner), docker_networks(ctx.runner), neighbor_files(ctx)
    volumes = ("docker", "volume", "ls", "--filter", f"name={PROJECT}", "--format", VOLUME_FORMAT)
    rows = docker_json(ctx.runner, volumes, "docker volume ls")
    foreign = [c.name for c in found if c.project != PROJECT]
    foreign += [n for n, p, _ in nets if p != PROJECT]
    foreign += [str(row.get("name")) for row in rows if row.get("project") != PROJECT]
    taken = [name for name in foreign if name in names or pattern.fullmatch(name)]
    return [
        verdict("имена", [f"имя занято не нашим стеком: {len(taken)}"] if taken else []),
        *port_verdicts(ctx, values.get("WEB_PORT", ""), found, declared),
        *subnet_verdicts(ctx, values, nets, declared),
        resources_verdict(ctx, limits, found),
    ]


def cmd_check(ctx: Context, _args: argparse.Namespace) -> int:
    """Предполётная проверка (A185; A181, третья строка; A189, вторая строка): строка на тему —
    «ок», отказ с причинами или названное исключение; машину и соседа только читает, пишет свои
    журнал и замок. Код не 0 — отказ хоть в одной строке."""
    with session(ctx, "check") as rep:
        env = read_env(ctx.files, ctx.paths.env_file)
        site_name(env.values.get("SITE_DOMAIN", ""))  # A184: до любой команды
        lines = file_verdicts(ctx, env)
        for topic, text, _bad in lines:
            rep.line(f"{topic}: {text}")
        urls = env.values.get("NEIGHBOR_HEALTH_URLS", "")
        before, more = neighbor_snapshot(ctx, urls), machine_verdicts(ctx, env.values)
        after = compare(before, neighbor_snapshot(ctx, urls))
        more.append(("снимок соседа", after.line.removeprefix("снимок соседа: "), after.red))
        for topic, text, _bad in more:
            rep.line(f"{topic}: {text}")
        lines += more
        rep.line(f"проверено строк: {len(lines)}, отказов: {sum(bad for *_, bad in lines)}")
        if refused := list(dict.fromkeys(topic for topic, _text, bad in lines if bad)):
            fix = "поправить и снова deploy.py check"
            raise RefusedError(f"отказ в строках: {', '.join(refused)} — {fix}")
    return 0


COMMANDS: dict[str, tuple[Callable[[Context, argparse.Namespace], int], str]] = {
    "check": (cmd_check, "предполётная проверка: файл сервера, имена, порт, подсеть, ресурсы"),
    "last": (cmd_last, "последний журнал хода и идёт ли выкат"),
    "env-init": (cmd_env_init, "файл окружения сервера, мастер-ключ и каталог входящих"),
    "put-secret": (cmd_put_secret, "секреты владельца из входящего файла в файл сервера"),
}
#: Позиционные аргументы команд: имя и что это.
ARGUMENTS: dict[str, tuple[tuple[str, str], ...]] = {
    "put-secret": (("file", "входящий файл из каталога входящих"),),
}


def main(argv: Sequence[str] | None = None, ctx: Context | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deploy.py", description="Выкат AI GEO Audit")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, (_command, summary) in COMMANDS.items():
        sub = commands.add_parser(name, help=summary)
        for argument, about in ARGUMENTS.get(name, ()):
            sub.add_argument(argument, help=about)
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
