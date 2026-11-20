"""Оракул стека Ч1.5а: собранный образ делает продукт, а не только собирается.

Чистая копия `git archive HEAD` с канарейками, сборка образа, шаги примеров, уборка своего
имени в конце прогона. Имя — `ai-geo-audit-oracle-` и 10 знаков sha256 пути рабочей копии: у
двух копий разные стеки. Только стандартная библиотека; пакет сервиса не импортирует — что
нужно от пакета, спрашивает внутри собранного образа. Время в выводе — UTC с Z; наружу ничего
не публикует. Уборка при обрыве и два прогона разом — поставка «Оракул стека в CI».

Запуск из рабочей копии: `python3 scripts/stack_oracle.py`. Код 0 — все шаги прошли, 1 — есть
красные, 2 — окружение: Docker не отвечает или копия не собралась.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = "deploy/backend/Dockerfile"
COMPOSE = "docker-compose.yml"
#: Адрес web для доверия api — петля, выдуманный: сам web — Ч1.5б.
WEB_ADDRESS = "127.0.0.1"
DB_URL = "postgresql+asyncpg://audit:{}@db:5432/audit"
WHITELIST = DOCKERFILE + ".dockerignore"
TARGET = "backend"
PACKAGE = "backend/src/ai_geo_audit"
#: Открытые пути белого списка контекста (A61).
OPEN_PATHS = (
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/src",
    "backend/alembic.ini",
    "backend/migrations",
)
#: Канарейка в открытом каталоге под закрытым именем: её держит повторное закрытие `.env*`.
DEEP_CANARY = "backend/src/ai_geo_audit/.env.local"
#: Имена из списков проверки старта; шаг A64 сверяет их со списками пакета в образе.
BUILD_ARG_NAMES = (
    "API_TRUSTED_PROXIES",
    "DATABASE_URL",
    "JWT_SECRET",
    "KEYS_MASTER_KEY_FILE",
    "REDIS_URL",
)
DEV_TOOLS = ("uv", "pytest", "uvloop")
PROBE_DATA = "_oracle_probe.txt"
APP_UID = "10001"
CACHE = re.compile(r"(^|/)__pycache__(/|$)|\.py[co]$")
#: Пределы команд, секунды: зависший шаг называется своим пределом, а не висит.
LIMITS = {"docker": 30, "build": 1200, "run": 120, "export": 300, "up": 300}
#: Запрос здоровья из разового контейнера в сети стека тем же образом: слышит ли api сеть.
NET_PROBE = """
import sys, urllib.request
try:
    status = urllib.request.urlopen("http://api:{port}/api/health", timeout=5).status
except OSError as error:
    sys.exit(f"{{type(error).__name__}}: {{error}}")
sys.exit(0 if status == 200 else f"ответ {{status}}")
"""
UP_HINT = "Docker не отвечает: поднимите Docker Desktop (open -a Docker) или службу docker"
#: Что оракул спрашивает у собранного образа: версия Python, инструменты, файлы, списки.
INSPECT = """
import importlib.util, json, os, pathlib, shutil, sys
import ai_geo_audit
from ai_geo_audit.config import startup_checks as sc
def files(root):
    return sorted(str(p.relative_to(root)) for p in pathlib.Path(root).rglob("*") if p.is_file())
print(json.dumps({
    "python": sys.version.split()[0],
    "dev": [name for name in sys.argv[1:] if importlib.util.find_spec(name)],
    "uv": shutil.which("uv"),
    "package": files(pathlib.Path(ai_geo_audit.__file__).parent),
    "workdir": files(os.getcwd()),
    "lists": sorted(set().union(*map(sc.required, ("api", "worker", "migrate")))),
}))
"""


class StepFailed(RuntimeError):
    """Шаг красный; текст — причина без секретов."""


def stack_name(path: Path) -> str:
    """Имя стека оракула по пути рабочей копии (A61, A75)."""
    return "ai-geo-audit-oracle-" + hashlib.sha256(str(path).encode()).hexdigest()[:10]


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(text: str) -> datetime | None:
    """Время Docker (RFC 3339, до наносекунд); нулевое `0001-01-01…` — None: не стартовал."""
    found = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d{1,6})?", text or "")
    if not found or found[1].startswith("0001-"):
        return None
    return datetime.fromisoformat(found[1] + (found[2] or "")).replace(tzinfo=UTC)


def top_uids(text: str) -> set[str]:
    """Пользователи процессов из `docker compose top`: столбец UID под строкой заголовка. Вид у
    версий разный: у 5.x первые столбцы — SERVICE и #, у 2.x — блок на контейнер с именем над
    заголовком и пустой строкой между блоками."""
    uids: set[str] = set()
    column: int | None = None
    for line in text.splitlines():
        words = line.split()
        if "UID" in words and "PID" in words:
            column = words.index("UID")
        elif not words:
            column = None
        elif column is not None and len(words) > column:
            uids.add(words[column])
    return uids


@dataclass
class Scan:
    """Что нашлось в выгрузке: сколько файлов просмотрено, где метка, где `.env*`, их имена."""

    scanned: int = 0
    hits: list[str] = field(default_factory=list)
    envs: list[str] = field(default_factory=list)
    names: set[str] = field(default_factory=set)


def scan_tar(stream: IO[bytes], label: str, prefix: str = "") -> Scan:
    """Все обычные файлы выгрузки под `prefix`: метка в содержимом или в имени, `.env*` (A61)."""
    scan, mark = Scan(), label.encode()
    with tarfile.open(fileobj=stream, mode="r|*") as archive:
        for member in archive:
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            name = member.name.removeprefix(prefix)
            scan.scanned += 1
            scan.names.add(name)
            data = archive.extractfile(member).read()
            if mark in data or label in name:
                scan.hits.append(name)
            if name.rsplit("/", 1)[-1].startswith(".env"):
                scan.envs.append(name)
    return scan


def label_problems(scan: Scan) -> list[str]:
    """Метки и `.env*` быть не должно; ноль просмотренных файлов — отказ, а не «чисто»."""
    if not scan.scanned:
        return ["просмотрено файлов 0 — выгрузка пуста, судить не по чему"]
    found = [f"метка канарейки в {len(scan.hits)} файлах: {scan.hits[:3]}"] if scan.hits else []
    return found + ([f"файлы .env* в образе: {scan.envs[:3]}"] if scan.envs else [])


def context_problems(names: set[str]) -> list[str]:
    """В контексте сборки ровно пять открытых путей и ни одного файла вне них (A61)."""

    def root(name: str) -> str | None:
        return next((p for p in OPEN_PATHS if name == p or name.startswith(p + "/")), None)

    problems = [f"нет открытого пути {p}" for p in OPEN_PATHS if p not in map(root, names)]
    outside = sorted(name for name in names if root(name) is None)
    problems += [f"в контексте лишнее: {outside[:5]}"] if outside else []
    return problems + ([f"в контексте канарейка {DEEP_CANARY}"] if DEEP_CANARY in names else [])


def compare(what: str, copy: set[str], image: set[str]) -> list[str]:
    """Сверка списков файлов в обе стороны (A62)."""
    missing, extra = sorted(copy - image), sorted(image - copy)
    return [f"{what}: нет в образе — {', '.join(missing)}"] * bool(missing) + [
        f"{what}: лишние в образе — {', '.join(extra)}"
    ] * bool(extra)


def files_under(root: Path) -> set[str]:
    """Файлы копии без кэша Python и без канареек `.env*` (их держит белый список, A61)."""
    names = (str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    return {n for n in names if not CACHE.search(n) and not n.rsplit("/", 1)[-1].startswith(".env")}


_UV_PIN = re.compile(r'\buv==(?P<pip>[\w.]+)|required-version\s*=\s*"==(?P<required>[\w.]+)"')
_UV_IMAGE = re.compile(r"^FROM\s+ghcr\.io/astral-sh/uv:(?P<tag>[\w.]+)@", re.MULTILINE)


def uv_versions(root: Path) -> tuple[dict[str, str], list[str], str]:
    """Версии uv по местам — образ, файл проекта, файлы CI — и что с ними не так (A60). Файлов
    CI нет — это говорится словами: пустая сверка не выглядит зелёной."""
    image = _UV_IMAGE.search((root / DOCKERFILE).read_text(encoding="utf-8"))
    versions = {"Dockerfile": image["tag"]} if image else {}
    workflows = root / ".github" / "workflows"
    places = [root / "backend" / "pyproject.toml", *sorted(workflows.glob("*.y*ml"))]
    for place in (p for p in places if p.is_file()):
        for number, line in enumerate(place.read_text(encoding="utf-8").splitlines(), 1):
            for pin in _UV_PIN.finditer(line):
                versions[f"{place.name}:{number}"] = pin["pip"] or pin["required"]
    ci = [place for place in versions if not place.startswith(("Dockerfile", "pyproject.toml"))]
    problems = ["CI есть, а версии uv в нём нет"] * (workflows.is_dir() and not ci)
    problems += [f"версии uv разошлись: {versions}"] * (len(set(versions.values())) > 1)
    problems += ["в Dockerfile нет образа uv"] * (not image)
    return (
        versions,
        problems,
        "" if workflows.is_dir() else "файлов CI нет — сверять uv с CI не с чем",
    )


def minor(text: str) -> str:
    """Минорная версия Python из строки вида `>=3.12,<3.13` или `==3.12.*`."""
    found = re.search(r"\d+\.\d+", text)
    return found.group(0) if found else "?"


def requires_python(text: str) -> str:
    """Минорная версия из `requires-python` файла проекта или замка."""
    found = re.search(r'requires-python\s*=\s*"([^"]+)"', text)
    return minor(found.group(1)) if found else "?"


def without_cache(names: list[str]) -> set[str]:
    return {name for name in names if not CACHE.search(name)}


@dataclass
class Docker:
    """Один исполнитель команд Docker: предел на каждую команду и учёт своего для уборки."""

    containers: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)

    def result(
        self, *args: str, limit: str = "run", **kw: Any
    ) -> subprocess.CompletedProcess[bytes]:
        """Команда Docker под пределом; `stdin` — байты на вход, `env` — окружение команды."""
        seconds, stdin = LIMITS[limit], kw.pop("stdin", None)
        try:
            return subprocess.run(
                ["docker", *args],
                input=stdin,
                capture_output=True,
                timeout=seconds,
                check=False,
                **kw,
            )
        except subprocess.TimeoutExpired:
            raise StepFailed(f"docker {' '.join(args[:2])} не уложился в {seconds} с") from None

    def __call__(self, *args: str, limit: str = "run", check: bool = True, **kw: Any) -> str:
        done = self.result(*args, limit=limit, **kw)
        if check and done.returncode:
            tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-6:]
            raise StepFailed(f"docker {args[0]} — код {done.returncode}: {' | '.join(tail)}")
        return done.stdout.decode("utf-8", "replace")

    def cleanup(self) -> list[str]:
        """Убрать свои контейнеры и образы; что не убралось — назвать."""
        plan = [
            (("rm", "-f", "-v", c), ("ps", "-aq", "--filter", f"name=^{c}$"), c)
            for c in dict.fromkeys(self.containers)
        ]
        plan += [
            (("image", "rm", "-f", i), ("image", "ls", "-q", i), i)
            for i in dict.fromkeys(self.images)
        ]
        left = []
        for remove, probe, what in plan:
            try:
                self(*remove, limit="docker", check=False)
                if self(*probe, limit="docker", check=False).strip():
                    left.append(what)
            except StepFailed:
                left.append(what)
        return left


@dataclass
class Run:
    """Прогон: копия, имя стека, метка канареек, выдуманные значения сборки, узнанное шагами."""

    docker: Docker
    copy: Path
    name: str
    label: str
    args: dict[str, str]
    facts: dict[str, Any] = field(default_factory=dict)
    #: Секреты стека выдумывает оракул: пароль 24 знака hex и секрет подписи 64 знака — проверка
    #: старта в бою их принимает (A21, A3).
    password: str = field(default_factory=lambda: secrets.token_hex(12))
    jwt: str = field(default_factory=lambda: secrets.token_hex(32))

    @property
    def image(self) -> str:
        return f"{self.name}-backend:oracle"

    def export(self, image: str, prefix: str = "") -> Scan:
        """Выгрузка файлов образа через остановленный контейнер; разбор — `scan_tar`."""
        container = f"{self.name}-export"
        self.docker.containers.append(container)
        self.docker("create", "--name", container, image, limit="docker")
        tar = self.copy.parent / f"{container}.tar"
        self.docker("export", "--output", str(tar), container, limit="export")
        self.docker("rm", "-f", container, limit="docker")
        with tar.open("rb") as stream:
            scan = scan_tar(stream, self.label, prefix)
        tar.unlink()
        return scan

    def stack_env(self, migrate_password: str | None = None) -> dict[str, str]:
        """Окружение compose: переменные сервера стека без чужих `COMPOSE_*`."""
        clean = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
        return clean | {
            "BACKEND_IMAGE": self.image,
            "DB_PASSWORD": self.password,
            "MIGRATE_DATABASE_URL": DB_URL.format(migrate_password or self.password),
            "API_DATABASE_URL": DB_URL.format(self.password),
            "JWT_SECRET": self.jwt,
            "WEB_ADDRESS": WEB_ADDRESS,
            "KEYS_MASTER_KEY_HOST_FILE": str(self.copy.parent / "master.key"),
        }

    def compose(self, *args: str, wrong: str | None = None, **kw: Any) -> str:
        """Команда compose стека оракула: своё имя, файл назван явно, `.env` копии не читается."""
        base = [
            "compose",
            "-p",
            self.name,
            "-f",
            str(self.copy / COMPOSE),
            "--env-file",
            os.devnull,
        ]
        return self.docker(*base, *args, env=self.stack_env(wrong), **kw)

    def compose_result(self, *args: str, wrong: str | None = None, **kw: Any) -> Any:
        base = [
            "compose",
            "-p",
            self.name,
            "-f",
            str(self.copy / COMPOSE),
            "--env-file",
            os.devnull,
        ]
        return self.docker.result(*base, *args, env=self.stack_env(wrong), **kw)

    def inspect(self, service: str) -> dict[str, Any]:
        """Состояние контейнера сервиса стека по `docker inspect`."""
        ids = self.compose("ps", "--all", "--quiet", service, limit="docker").split()
        if not ids:
            raise StepFailed(f"контейнера {service} нет")
        return json.loads(self.docker("inspect", ids[0], limit="docker"))[0]

    def cleanup(self) -> list[str]:
        """Уборка своего имени: стек с томами, контейнеры и образы; остаток — по меткам compose."""
        if (self.copy / COMPOSE).is_file():
            try:
                self.compose("down", "--volumes", "--remove-orphans", limit="up", check=False)
            except StepFailed:
                pass
        left = self.docker.cleanup()
        mark = f"label=com.docker.compose.project={self.name}"
        for kind, flags in (("container", ["-a"]), ("volume", []), ("network", [])):
            ids = self.docker(kind, "ls", "-q", *flags, "--filter", mark, check=False).split()
            left += [f"{kind} {item}" for item in ids]
        return left


def output(done: subprocess.CompletedProcess[bytes]) -> str:
    return (done.stdout + done.stderr).decode("utf-8", "replace")


def health(run: Run, service: str) -> str | None:
    return run.inspect(service)["State"].get("Health", {}).get("Status")


def wait_health(run: Run, service: str, status: str, limit: float) -> float:
    """Ждать состояния здоровья сервиса не дольше `limit` с; время монотонных часов."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if health(run, service) == status:
            return time.monotonic()
        time.sleep(0.5)
    raise StepFailed(f"{service} не стал {status} за {limit:g} с")


def alembic(run: Run, command: str) -> list[str]:
    """Ревизии alembic из образа: `current` или `heads` разовым migrate."""
    out = run.compose("run", "--rm", "-T", "--no-deps", "migrate", "alembic", command)
    return sorted(re.findall(r"(?m)^(\w+)(?: \(head\))?\s*$", out))


def plant(copy: Path, label: str) -> None:
    """Канарейки с меткой прогона и подложенный файл данных пакета (A61, A62)."""
    places = [copy, copy / "backend", copy / PACKAGE]
    files = [place / name for place in places for name in (".env", ".env.local", ".env.prod")]
    tops = [p for p in copy.iterdir() if p.is_dir() and p.name != "backend"]
    invented = [copy / "private", copy / f"zz-{label[-6:]}", copy / ".git", copy / "backend/tests"]
    files += [top / f"canary-{label}.txt" for top in tops + invented]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{label}\n", encoding="utf-8")
    (copy / PACKAGE / PROBE_DATA).write_text("данные пакета для пробы A62\n", encoding="utf-8")


def step_build(run: Run) -> str:
    """A60, A64: сборка цели `backend` из чистой копии; выдуманные значения — в `--build-arg`."""
    args = [f"--build-arg={name}={value}" for name, value in run.args.items()]
    dockerfile = str(run.copy / DOCKERFILE)
    run.docker.images.append(run.image)
    build = ["build", "--file", dockerfile, "--target", TARGET, "--tag", run.image]
    run.docker(*build, *args, str(run.copy), limit="build")
    container = f"{run.name}-inspect"
    run.docker.containers.append(container)
    flags = ["--rm", "--name", container, "--network", "none", "--entrypoint", "python"]
    run.facts["inspect"] = json.loads(
        run.docker("run", *flags, run.image, "-c", INSPECT, *DEV_TOOLS)
    )
    config = run.docker(
        "image", "inspect", "--format", "{{json .Config}}", run.image, limit="docker"
    )
    run.facts["config"] = json.loads(config)
    return f"образ {run.image}"


def step_python(run: Run) -> str:
    """A60: Python образа — 3.12.x, та же минорная в `requires-python` и в `uv.lock`; без
    инструментов разработки."""
    facts = run.facts["inspect"]
    pyproject = (run.copy / "backend/pyproject.toml").read_text(encoding="utf-8")
    lock = (run.copy / "backend/uv.lock").read_text(encoding="utf-8")
    wanted = {requires_python(pyproject), requires_python(lock)}
    if wanted != {minor(facts["python"])} or not facts["python"].startswith("3.12."):
        raise StepFailed(f"Python {facts['python']}, а в pyproject и uv.lock — {sorted(wanted)}")
    if facts["dev"] or facts["uv"]:
        raise StepFailed(f"в образе инструменты разработки: {facts['dev']}, uv — {facts['uv']}")
    versions, problems, note = uv_versions(run.copy)
    if problems:
        raise StepFailed("; ".join(problems))
    uv = f"uv {set(versions.values()).pop()} в {len(versions)} местах" + (
        f"; {note}" if note else ""
    )
    return (
        f"Python {facts['python']}; requires-python и uv.lock — {minor(facts['python'])}; "
        f"{', '.join(DEV_TOOLS)} — ModuleNotFoundError, uv в PATH нет; {uv}"
    )


def step_context(run: Run) -> str:
    """A61: проба контекста — образ поверх собранного с `COPY .` из того же контекста."""
    probe = run.copy.parent / "ctx"
    probe.mkdir()
    (probe / "ctx.Dockerfile").write_text(f"FROM {run.image}\nCOPY . /ctx/\n", encoding="utf-8")
    shutil.copy(run.copy / WHITELIST, probe / "ctx.Dockerfile.dockerignore")
    image = f"{run.name}-context:oracle"
    run.docker.images.append(image)
    dockerfile = str(probe / "ctx.Dockerfile")
    run.docker("build", "--file", dockerfile, "--tag", image, str(run.copy), limit="build")
    scan = run.export(image, "ctx/")
    problems = context_problems(scan.names) + label_problems(scan)
    if problems:
        raise StepFailed("; ".join(problems))
    return f"в контексте {scan.scanned} файлов под пятью открытыми путями, канареек и метки нет"


def step_image_files(run: Run) -> str:
    """A61, A62: метки нет ни в одном файле образа, `.env*` нет нигде."""
    scan = run.export(run.image)
    if problems := label_problems(scan):
        raise StepFailed("; ".join(problems))
    return f"просмотрено файлов {scan.scanned}: метки и .env* нет"


def step_package(run: Run) -> str:
    """A62: файлы пакета в окружении образа — как в копии, с подложенным; в рабочем каталоге —
    только `alembic.ini` и `migrations/` с теми же ревизиями."""
    facts = run.facts["inspect"]
    migrations = {f"migrations/{n}" for n in files_under(run.copy / "backend/migrations")}
    package, workdir = without_cache(facts["package"]), without_cache(facts["workdir"])
    problems = compare("пакет", files_under(run.copy / PACKAGE), package)
    problems += compare("рабочий каталог", {"alembic.ini"} | migrations, workdir)
    if PROBE_DATA not in facts["package"]:
        problems.append(f"подложенного {PROBE_DATA} в образе нет")
    if problems:
        raise StepFailed("; ".join(problems))
    return (
        f"пакет — {len(package)} файлов, как в копии; рабочий каталог — alembic.ini и "
        f"{len(migrations)} файлов migrations/"
    )


def step_user(run: Run) -> str:
    """A63: пользователь образа — числом, 10001."""
    user = run.facts["config"].get("User")
    if user != APP_UID:
        raise StepFailed(f"Config.User — {user!r}, а не {APP_UID}")
    return f"Config.User — {APP_UID}"


def step_secrets(run: Run) -> str:
    """A64: выдуманных значений `--build-arg` нет ни в истории образа, ни в `Config.Env`; нет и
    инструкций `ARG`/`ENV` с этими именами. Список имён — тот же, что у проверки старта."""
    show = ["history", "--no-trunc", "--format", "{{.CreatedBy}}"]
    history = run.docker(*show, run.image, limit="docker")
    env = json.dumps(run.facts["config"].get("Env") or [])
    leaks = [name for name, value in run.args.items() if value in history or value in env]
    named = [name for name in run.args if re.search(rf"\b(ARG|ENV)\s+{name}\b", history)]
    problems = [f"значения в образе: {leaks}"] * bool(leaks)
    problems += [f"ARG или ENV с именами: {named}"] * bool(named)
    if run.facts["inspect"]["lists"] != sorted(BUILD_ARG_NAMES):
        problems.append(f"списки проверки старта — {run.facts['inspect']['lists']}")
    if problems:
        raise StepFailed("; ".join(problems))
    return f"значений {len(run.args)} имён нет в истории и Config.Env"


def step_rules_on_file(run: Run) -> str:
    """A73: правила на настоящем файле внутри собранного образа. Файл назван явно — чужой
    `docker-compose.override.yml` не подмешается; профили включены все; без `-p`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
    base = ["compose", "-f", str(run.copy / COMPOSE), "--env-file", os.devnull, "--profile", "*"]
    raw = run.docker(*base, "config", "--no-interpolate", "--format", "json", env=env)
    services = run.docker(*base, "config", "--no-interpolate", "--services", env=env).split()
    container = f"{run.name}-rules"
    run.docker.containers.append(container)
    flags = ["--rm", "-i", "--name", container, "--network", "none", "--entrypoint", "python"]
    rules = ["-m", "ai_geo_audit.ops.stack_rules"]
    done = run.docker.result("run", *flags, run.image, *rules, stdin=raw.encode())
    lines = output(done).strip().splitlines()
    if done.returncode or lines[-1:] != [f"итог: сервисов {len(services)}, правил 16, нарушений 0"]:
        raise StepFailed(f"правила — код {done.returncode}: {' | '.join(lines[-5:])}")
    return f"{lines[-1]}; config --services — {len(services)}"


def step_up(run: Run) -> str:
    """A65, A63: стек одной командой — migrate вышел 0 до старта api, api healthy; ревизия в базе
    равна голове, обе — из образа; migrate и процессы api — от uid 10001."""
    done = run.compose_result("up", "--detach", "--wait", "--wait-timeout", "240", limit="up")
    if done.returncode:
        raise StepFailed(f"up --wait — код {done.returncode}: {output(done)[-500:]}")
    migrate, api = run.inspect("migrate"), run.inspect("api")
    finished, started = (
        parse_time(migrate["State"]["FinishedAt"]),
        parse_time(api["State"]["StartedAt"]),
    )
    current, heads = alembic(run, "current"), alembic(run, "heads")
    uid = run.compose("run", "--rm", "-T", "--no-deps", "migrate", "id", "-u").strip()
    uids = top_uids(run.compose("top", "api", limit="docker"))
    problems = [f"migrate вышел с кодом {migrate['State']['ExitCode']}"] * bool(
        migrate["State"]["ExitCode"]
    )
    problems += ["api стартовал не после выхода migrate"] * (
        not (finished and started and started > finished)
    )
    problems += [f"api — {health(run, 'api')}"] * (health(run, "api") != "healthy")
    problems += [f"alembic current {current}, heads {heads}"] * (not current or current != heads)
    problems += [f"id -u в migrate — {uid}, процессы api — {sorted(uids)}"] * (
        uid != APP_UID or uids != {APP_UID}
    )
    if problems:
        raise StepFailed("; ".join(problems))
    return f"migrate — код 0 до старта api; alembic current = heads {current}; uid {APP_UID}"


def step_health(run: Run) -> str:
    """A69: api healthy не позже 50 с от старта (20 с + 3 × 10 с); проба внутри — код 0; запрос
    здоровья из разового контейнера в сети стека — 200: api слышит сеть, а не только петлю."""
    api = run.inspect("api")
    started = parse_time(api["State"]["StartedAt"])
    oks = [parse_time(e["End"]) for e in api["State"]["Health"]["Log"] if e["ExitCode"] == 0]
    seconds = (min(t for t in oks if t) - started).total_seconds()
    probe = run.compose_result("exec", "-T", "api", "python", "-m", "ai_geo_audit.api.probe")
    port = dict(e.split("=", 1) for e in api["Config"]["Env"]).get("API_PORT", "8107")
    once = ["run", "--rm", "-T", "--no-deps", "--entrypoint", "python", "migrate", "-c"]
    net = run.compose_result(*once, NET_PROBE.format(port=port))
    if seconds > 50 or probe.returncode or net.returncode:
        raise StepFailed(
            f"healthy через {seconds:.1f} с; проба внутри — код {probe.returncode}; из сети "
            f"стека — код {net.returncode}: {output(net).strip()[-120:]}"
        )
    return (
        f"healthy через {seconds:.1f} с от старта api; проба в контейнере — код 0; из сети стека "
        f"http://api:{port}/api/health — 200"
    )


def step_repeat(run: Run) -> str:
    """A66: migrate второй раз — код 0, ревизия прежняя, «Running upgrade» в выводе нет."""
    before = alembic(run, "current")
    done = run.compose_result("run", "--rm", "-T", "--no-deps", "migrate")
    after = alembic(run, "current")
    if done.returncode or "Running upgrade" in output(done) or after != before:
        raise StepFailed(f"повтор — код {done.returncode}, ревизия {before} → {after}")
    return f"код 0, ревизия {after}, «Running upgrade» нет"


def step_outage(run: Run) -> str:
    """A69: db остановлена — api unhealthy через 20–35 с; db снова healthy — api healthy не позже
    15 с; перезапусков api — 0."""
    run.compose("stop", "db", limit="up")
    stopped = time.monotonic()
    unhealthy = wait_health(run, "api", "unhealthy", 60) - stopped
    run.compose("start", "db", limit="up")
    db_back = wait_health(run, "db", "healthy", 90)
    back = wait_health(run, "api", "healthy", 60) - db_back
    restarts = run.inspect("api")["RestartCount"]
    times = f"unhealthy через {unhealthy:.1f} с после остановки db, healthy через {back:.1f} с"
    if not 20 <= unhealthy <= 35 or back > 15 or restarts:
        raise StepFailed(f"{times} после db, перезапусков {restarts}")
    return f"{times} после возврата db; перезапусков 0"


def step_bad_migrate(run: Run) -> str:
    """A67: у migrate неверный пароль базы, у api верный — up --wait не 0; migrate назван с кодом
    выхода; api не стартовал (created, StartedAt нулевое); db healthy; пароля нет в выводе."""
    run.compose("down", "--volumes", "--remove-orphans", limit="up")
    wrong = secrets.token_hex(12)
    done = run.compose_result(
        "up", "--detach", "--wait", "--wait-timeout", "240", wrong=wrong, limit="up"
    )
    migrate, api = run.inspect("migrate"), run.inspect("api")
    code = migrate["State"]["ExitCode"]
    seen = output(done) + run.compose("logs", "--no-color", "migrate", limit="docker")
    problems = ["up --wait — код 0"] * (done.returncode == 0) + ["migrate — код 0"] * (code == 0)
    problems += [f"api — {api['State']['Status']}"] * (
        api["State"]["Status"] != "created" or parse_time(api["State"]["StartedAt"]) is not None
    )
    problems += [f"db — {health(run, 'db')}"] * (health(run, "db") != "healthy")
    problems += ["пароль в выводе"] * (wrong in seen or run.password in seen)
    if problems:
        raise StepFailed("; ".join(problems))
    return f"up --wait — код {done.returncode}; migrate — код выхода {code}; api — created"


#: Шаги: примеры, название, функция, нужен ли он следующим шагам.
STEPS: list[tuple[str, str, Callable[[Run], str], bool]] = [
    ("A60 A64", "сборка из чистой копии", step_build, True),
    ("A60", "Python и инструменты", step_python, False),
    ("A61", "проба контекста", step_context, False),
    ("A61 A62", "метка и .env* в файлах образа", step_image_files, False),
    ("A62", "файлы пакета и рабочего каталога", step_package, False),
    ("A63", "пользователь образа", step_user, False),
    ("A64", "секреты сборки", step_secrets, False),
    ("A73", "правила на настоящем docker-compose.yml", step_rules_on_file, False),
    ("A65 A63", "подъём стека и migrate", step_up, True),
    ("A69", "здоровье api после старта", step_health, False),
    ("A66", "повтор migrate", step_repeat, False),
    ("A69", "остановка и старт db", step_outage, False),
    ("A67", "неверный пароль у migrate", step_bad_migrate, False),
]


def verdict(red: int, left: list[str]) -> tuple[int, str]:
    """Итог прогона: красные шаги или своё, оставшееся после уборки, — код 1 и «КРАСНОЕ»."""
    line = f"итог: шагов {len(STEPS)}, красных {red}"
    if left:
        return 1, f"{line}; КРАСНОЕ: после уборки осталось {', '.join(left)}"
    return (1 if red else 0), line


def copy_head(target: Path) -> None:
    """Чистая копия: `git archive HEAD` — только закоммиченное, без личных каталогов."""
    command = ["git", "-C", str(ROOT), "archive", "--format=tar", "HEAD"]
    done = subprocess.run(command, capture_output=True, timeout=LIMITS["docker"], check=True)
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
        archive.extractall(target, filter="data")


def main() -> int:
    name, docker = stack_name(ROOT), Docker()
    try:
        server = docker("version", "--format", "{{.Server.Version}}", limit="docker").strip()
    except (StepFailed, OSError):
        print(f"{now()} {UP_HINT}")
        return 2
    print(f"{now()} оракул: стек {name}, Docker {server}")
    red = 0
    with tempfile.TemporaryDirectory(prefix=f"{name}-") as tmp:
        copy = Path(tmp) / "copy"
        try:
            copy_head(copy)
        except (subprocess.SubprocessError, OSError) as failure:
            print(f"{now()} копия не собралась: {type(failure).__name__}")
            return 2
        label = f"oracle-canary-{secrets.token_hex(8)}"
        plant(copy, label)
        key = Path(tmp) / "master.key"
        key.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        key.chmod(0o644)  # читает uid 10001 в контейнере; права машины переходят как есть
        values = {n: f"oracle-build-{n.lower()}-{secrets.token_hex(6)}" for n in BUILD_ARG_NAMES}
        run = Run(docker, copy, name, label, values)
        try:
            for examples, title, step, needed in STEPS:
                try:
                    print(f"{now()} {examples} {title} — ок: {step(run)}")
                except (StepFailed, KeyError, TypeError, ValueError) as failure:
                    red += 1
                    print(f"{now()} {examples} {title} — КРАСНОЕ: {failure}")
                    if needed:
                        break
        finally:
            left = run.cleanup()
            print(f"{now()} уборка: " + (f"осталось {left}" if left else "своего не осталось"))
    code, line = verdict(red, left)
    print(f"{now()} {line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
