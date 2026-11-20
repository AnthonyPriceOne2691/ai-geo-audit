"""Сканер образа (A124–A129): Trivy файлом выпуска, скан каждого образа репозитория, опись.

- `install --dir D` — архив выпуска во временный файл, sha256 — до распаковки; из архива только
  обычный файл `trivy`, права 755. Отказ — код 1 с причиной, каталог установки пуст.
- `run --work W [--trivy T]` — страж списка скана и файла исключений; база уязвимостей и её
  даты; канарейка — известная уязвимость находится, версия сканера — из записи выпуска; каждый
  образ списка — `docker build`, `docker save` и скан файла: сокета Docker сканер не получает.
- `sbom --work W [--trivy T]` — опись CycloneDX того же файла образа: пакеты Python-окружения
  образа — все в описи с той же версией; число компонентов и sha256 описи — в журнал.

Только стандартная библиотека; `trivy` и `docker` зовутся через один исполнитель — его
подменяют тесты. У каждого вызова `trivy` — без проверки версии и телеметрии: наружу он ходит
только за базой. `trivy version` не зовётся: в 0.76.2 этих флагов у него нет. Версия сканера —
из отчёта скана, даты базы — из её метаданных в кеше: тот же файл читает `trivy version`.
Время в выводе — UTC с Z. Код 0 — чисто, 1 — красное.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Release:
    """Выпуск сканера: версия, адрес архива, sha256 архива."""

    version: str
    url: str
    sha256: str


#: Trivy — одна запись: версия, адрес и сумма (слово владельца 2026-11-20T09:38Z). Новая версия —
#: выпуск старше 14 суток, сумма из файла сумм выпуска, подпись, слово владельца.
RELEASE = Release(
    version="0.76.2",
    url="https://github.com/aquasecurity/trivy/releases/download/v0.76.2/"
    "trivy_0.76.2_Linux-64bit.tar.gz",
    sha256="bc0fc726e017b8927a62b2d3531d7a84ee01769daf5c99ed866dced54089fdbf",
)


@dataclass(frozen=True)
class Image:
    """Образ списка скана: файл сборки, цель, ОС базы, есть ли окружение Python."""

    dockerfile: str
    target: str
    os: str
    python: bool

    @property
    def tag(self) -> str:
        return f"ai-geo-audit-scan:{self.target}"


#: Список скана — каждый образ, который собирает репозиторий; страж A126 сверяет его с `deploy/`.
IMAGES = (Image("deploy/backend/Dockerfile", "backend", "debian", python=True),)
IGNORE_FILE = ".trivyignore"
OWNER = "by=human:owner"
#: У каждого вызова `trivy` (A129): без проверки версии и телеметрии; только уязвимости пакетов
#: ОС и библиотек; свой предел времени у самого сканера.
QUIET = ("--skip-version-check", "--disable-telemetry")
SCAN = ("--scanners", "vuln", "--pkg-types", "os,library", "--timeout", "10m")
SEVERITY = ("CRITICAL", "HIGH")
#: Пределы, секунды: зависшая команда называется своим пределом, а не висит.
LIMITS = {"download": 300, "trivy": 900, "build": 1200, "save": 300, "run": 120}
#: Канарейка (A125): находка, пакет по PEP 503, версия, версия с исправлением.
CANARY = ("CVE-2020-14343", "pyyaml", "5.3", "5.4")
CANARY_FILE = "site-packages/PyYAML-5.3.dist-info/METADATA"
CANARY_METADATA = "Metadata-Version: 2.1\nName: PyYAML\nVersion: 5.3\n\n"
#: Пакеты Python-окружения образа: имя и версия каждого распределения (A127).
ENV_PROBE = (
    "import importlib.metadata as m, json; "
    "print(json.dumps(sorted({(d.name, d.version) for d in m.distributions() if d.name})))"
)
#: Поля находки в отчёте Trivy JSON (`Results[].Vulnerabilities[]`) — в порядке полей `Finding`.
FIELDS = ("VulnerabilityID", "PkgName", "InstalledVersion", "FixedVersion", "Severity")
_FINDING = re.compile(r"\S+\s+exp:(\d{4}-\d\d-\d\d)")
_DOCKERFILE = re.compile(r"(.+\.)?Dockerfile(\..+)?")


class ScanError(RuntimeError):
    """Шаг красный; текст — причина."""


#: Исполнитель команд: аргументы и предел в секундах; тесты подменяют его своим.
Runner = Callable[[list[str], int], subprocess.CompletedProcess[bytes]]


class Finding(NamedTuple):
    """Находка отчёта: идентификатор, пакет, версия, версия с исправлением, тяжесть."""

    ident: str
    package: str
    version: str
    fixed: str
    severity: str


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def say(text: str) -> None:
    print(f"{now()} {text}", flush=True)


def tail(err: bytes) -> str:
    return " | ".join(err.decode("utf-8", "replace").strip().splitlines()[-6:]) or "вывода нет"


def utc(text: object) -> str:
    """Время Trivy (RFC 3339, до наносекунд) — UTC с Z, до секунд; не время — как есть."""
    try:
        moment = datetime.fromisoformat(re.sub(r"\.\d+", "", str(text)))
    except ValueError:
        return f"«{text}»"
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def execute(argv: list[str], limit: int) -> subprocess.CompletedProcess[bytes]:
    """Настоящий исполнитель: команда без оболочки под пределом."""
    try:
        return subprocess.run(  # noqa: S603 -- trivy и docker с аргументами скрипта, без оболочки
            argv, capture_output=True, timeout=limit, check=False
        )
    except subprocess.TimeoutExpired:
        raise ScanError(f"{Path(argv[0]).name} {argv[1]} не уложился в {limit} с") from None
    except FileNotFoundError:
        raise ScanError(f"{argv[0]} не найден") from None


def download(url: str, path: Path) -> str:
    """Архив выпуска — во временный файл; sha256 считается по ходу."""
    digest, seconds = hashlib.sha256(), LIMITS["download"]
    with (
        urllib.request.urlopen(url, timeout=seconds) as response,  # noqa: S310 -- адрес выпуска
        path.open("wb") as out,
    ):
        while chunk := response.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()


def take_trivy(archive: Path, binary: Path) -> None:
    """Из архива — только обычный файл `trivy`: ссылка, каталог или его отсутствие — отказ."""
    with tarfile.open(archive, "r:gz") as tar:
        try:
            member = tar.getmember("trivy")
        except KeyError:
            raise ScanError("trivy нет в архиве") from None
        source = tar.extractfile(member) if member.isfile() else None
        if source is None:
            raise ScanError("trivy — не обычный файл")
        with source, binary.open("wb") as out:
            shutil.copyfileobj(source, out)
    binary.chmod(0o755)


def install(target: Path, release: Release = RELEASE) -> int:
    """A124: архив выпуска во временный файл, sha256 — до распаковки; из архива только обычный
    файл `trivy`, права 755. Отказ — код 1 и причина; каталог установки остаётся пустым."""
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="trivy-release-") as tmp:
        archive = Path(tmp) / "release.tar.gz"
        try:
            digest = download(release.url, archive)
            if digest != release.sha256:
                raise ScanError(f"sha256 не совпала: {digest}, в записи — {release.sha256}")
            take_trivy(archive, target / "trivy")
        except (ScanError, OSError, tarfile.TarError) as failure:
            say(f"установка Trivy {release.version} — КРАСНОЕ: {failure}")
            return 1
    say(f"Trivy {release.version} — {target / 'trivy'}; sha256 архива сверена до распаковки")
    return 0


def guard_problems(root: Path) -> list[str]:
    """A126: каждый файл сборки образа в `deploy/` на любой глубине — в списке скана, и у каждой
    строки списка файл есть. Лишний и пропавший названы по имени."""
    found = {
        path.relative_to(root).as_posix()
        for path in (root / "deploy").rglob("*")
        if path.is_file()
        and _DOCKERFILE.fullmatch(path.name)
        and not path.name.endswith(".dockerignore")
    }
    listed = {image.dockerfile for image in IMAGES}
    extra = [f"{name} — Dockerfile вне списка скана" for name in sorted(found - listed)]
    return extra + [f"{name} — в списке скана, а файла нет" for name in sorted(listed - found)]


def expiry(line: str) -> date | None:
    """Срок строки находки `ИД exp:ГГГГ-ММ-ДД`; другой вид строки или не дата — None."""
    found = _FINDING.fullmatch(line)
    try:
        return date.fromisoformat(found[1]) if found else None
    except ValueError:
        return None


def ignore_problems(text: str) -> list[str]:
    """A128: у строки находки — срок `exp:ГГГГ-ММ-ДД`; строкой выше — свой комментарий с причиной
    и словом владельца `by=human:owner` отдельным словом. Нарушение называет номер строки."""
    problems: list[str] = []
    above = ""
    for number, line in enumerate((raw.strip() for raw in text.splitlines()), 1):
        if line and not line.startswith("#"):
            where = f"{IGNORE_FILE}:{number}:"
            if expiry(line) is None:
                problems.append(f"{where} строка находки — не «ИД exp:ГГГГ-ММ-ДД»")
            words = above.lstrip("#").split() if above.startswith("#") else []
            reason = [word for word in words if word != OWNER and not word.startswith("at=")]
            if OWNER not in words or not re.search(r"\w", " ".join(reason)):
                problems.append(f"{where} строкой выше нет комментария с причиной и {OWNER}")
        above = line
    return problems


def parse(raw: bytes, what: str) -> dict[str, Any]:
    """Отчёт сканера: пустой или нечитаемый вывод — отказ, а не ноль находок (A126)."""
    if not raw.strip():
        raise ScanError(f"{what}: пустой вывод — отчёта нет, это не «находок нет»")
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise ScanError(f"{what}: вывод — не отчёт JSON: {raw[:60]!r}")
    return data


def findings(report: dict[str, Any]) -> list[Finding]:
    """Находки всех целей отчёта Trivy JSON: `Results[].Vulnerabilities[]`."""
    return [
        Finding(*(str(item.get(key) or "") for key in FIELDS))
        for result in report.get("Results") or []
        for item in result.get("Vulnerabilities") or []
    ]


def packages(report: dict[str, Any]) -> int:
    """Число пакетов отчёта: `Results[].Packages`; в JSON Trivy по умолчанию — все пакеты."""
    return sum(len(result.get("Packages") or []) for result in report.get("Results") or [])


def norm(name: str) -> str:
    """Имя пакета Python по PEP 503: регистр и знаки `-_.` не различаются."""
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass(frozen=True)
class Trivy:
    """Вызовы `trivy` одним исполнителем: у каждого — флаги A129, кеш базы в рабочем каталоге."""

    path: str
    cache: Path
    runner: Runner

    def __call__(self, command: str, *args: str) -> bytes:
        argv = [self.path, command, *QUIET, *SCAN, "--cache-dir", str(self.cache), *args]
        done = self.runner(argv, LIMITS["trivy"])
        if done.returncode:
            raise ScanError(f"trivy {command} — код {done.returncode}: {tail(done.stderr)}")
        return done.stdout

    def report(self, command: str, *args: str) -> dict[str, Any]:
        """Отчёт JSON с порогом скана — по базе этого прогона, без её обновления."""
        severity = ",".join(SEVERITY)
        raw = self(command, "--skip-db-update", "--format", "json", "--severity", severity, *args)
        return parse(raw, f"trivy {command}")

    def database(self) -> str:
        """База уязвимостей одна на прогон: качается здесь, сканы её не обновляют. Отказ —
        «база уязвимостей не получена» с причиной, а не «находок нет» (A126)."""
        try:
            self("image", "--download-db-only")
            meta = json.loads((self.cache / "db" / "metadata.json").read_text(encoding="utf-8"))
        except (ScanError, OSError, ValueError) as failure:
            raise ScanError(f"база уязвимостей не получена: {failure}") from None
        zero = not isinstance(meta, dict) or not meta.get("Version")
        if zero or str(meta.get("UpdatedAt", "0001-")).startswith("0001-"):
            raise ScanError(f"база уязвимостей не получена: метаданные — {meta}")
        built, got = utc(meta["UpdatedAt"]), utc(meta.get("DownloadedAt"))
        return f"база уязвимостей v{meta['Version']}: собрана {built}, получена {got}"


def canary(trivy: Trivy, work: Path) -> str:
    """A125: до скана образов известная уязвимость находится — каталог с PyYAML 5.3, порог скана
    образа, без файла исключений. Версия сканера в отчёте — та, что в записи выпуска."""
    metadata = work / "canary" / CANARY_FILE
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(CANARY_METADATA, encoding="utf-8")
    report = trivy.report("rootfs", "--ignorefile", "", str(work / "canary"))
    version = (report.get("Trivy") or {}).get("Version")
    if version != RELEASE.version:
        raise ScanError(f"сканер не той версии: {version}, в записи выпуска — {RELEASE.version}")
    ident, package, installed, fixed = CANARY
    if not any(
        (found.ident, norm(found.package), found.version) == (ident, package, installed)
        and fixed in re.split(r",\s*", found.fixed)
        for found in findings(report)
    ):
        raise ScanError(f"сканер не видит известную уязвимость: {ident} у PyYAML {installed}")
    return f"Trivy {version}; канарейка найдена: {ident} у PyYAML {installed}, исправление {fixed}"


def docker(runner: Runner, *args: str, limit: str) -> bytes:
    """Команда Docker тем же исполнителем; код не ноль — красное с хвостом её ошибок."""
    done = runner(["docker", *args], LIMITS[limit])
    if done.returncode:
        raise ScanError(f"docker {args[0]} — код {done.returncode}: {tail(done.stderr)}")
    return done.stdout


def save(image: Image, work: Path, runner: Runner) -> Path:
    """A126: образ этого коммита — сборка цели и файл `docker save`: сканер получает файл."""
    build = ["build", "--file", str(ROOT / image.dockerfile), "--target", image.target]
    docker(runner, *build, "--tag", image.tag, str(ROOT), limit="build")
    archive = work / f"{image.target}.tar"
    docker(runner, "save", "--output", str(archive), image.tag, limit="save")
    return archive


def verdict(image: Image, report: dict[str, Any]) -> bool:
    """A126: находки CRITICAL и HIGH с исправлением — таблица и красное; без исправления —
    списком, джобу не краснят. Ноль пакетов — отказ: сканер не видит образ."""
    count = packages(report)
    if not count:
        raise ScanError("пакетов 0 — сканер не видит образ, это не «находок нет»")
    found = sorted({item for item in findings(report) if item.severity in SEVERITY})
    fixed = [item for item in found if item.fixed]
    say(
        f"{image.target}: пакетов {count}; {' и '.join(SEVERITY)} — с исправлением {len(fixed)}, "
        f"без исправления {len(found) - len(fixed)}"
    )
    for item in found:
        if not item.fixed:
            say(f"{image.target}: без исправления — {' '.join(item[:3])} ({item.severity})")
    if fixed:
        say(f"{image.target}: КРАСНОЕ — находки с исправлением:")
        rows = [("идентификатор", "пакет", "версия", "исправление"), *(i[:4] for i in fixed)]
        print("\n".join(f"| {' | '.join(row)} |" for row in rows), flush=True)
    return bool(fixed)


def each(step: Callable[[Image], bool], title: str) -> int:
    """Шаг на каждый образ списка: красный образ не останавливает остальные; итог — код 1 или 0."""
    red = 0
    for image in IMAGES:
        try:
            red += step(image)
        except ScanError as failure:
            red += 1
            say(f"{image.target}: КРАСНОЕ: {failure}")
    say(f"итог {title}: образов {len(IMAGES)}, красных {red}")
    return 1 if red else 0


def run(trivy: str, work: Path, runner: Runner = execute) -> int:
    """A125, A126, A128: страж списка скана и исключений; база; канарейка; каждый образ списка —
    сборка, файл `docker save` и скан файла с исключениями. Код 1 — красное, итог джобы."""
    ignore = (ROOT / IGNORE_FILE).read_text(encoding="utf-8")
    if problems := guard_problems(ROOT) + ignore_problems(ignore):
        for problem in problems:
            say(f"КРАСНОЕ: {problem}")
        return 1
    work.mkdir(parents=True, exist_ok=True)
    scanner = Trivy(trivy, work / "cache", runner)
    try:
        say(scanner.database())
        say(canary(scanner, work))
    except ScanError as failure:
        say(f"КРАСНОЕ: {failure}")
        return 1

    def scan_image(image: Image) -> bool:
        archive = save(image, work, runner)
        flags = ("--ignorefile", str(ROOT / IGNORE_FILE), "--input", str(archive))
        return verdict(image, scanner.report("image", *flags))

    return each(scan_image, "скана")


def python_env(image: Image, runner: Runner) -> list[tuple[str, str]]:
    """A127: пакеты Python-окружения образа — `importlib.metadata` в контейнере без сети."""
    flags = ("--rm", "--network", "none", "--entrypoint", "python")
    out = docker(runner, "run", *flags, image.tag, "-c", ENV_PROBE, limit="run")
    try:
        pairs = [(str(name), str(version)) for name, version in json.loads(out)]
    except (ValueError, TypeError):
        raise ScanError(f"окружение Python образа не прочитано: {out[:60]!r}") from None
    if not pairs:
        raise ScanError("в окружении Python образа пакетов 0 — сверять не с чем")
    return pairs


def bom_problems(bom: dict[str, Any], env: list[tuple[str, str]], os_name: str) -> list[str]:
    """A127: опись не пуста; ОС — та, что в базе образа; каждый пакет окружения — компонент
    `pkg:pypi` с той же версией, имена — по PEP 503."""
    components = bom.get("components") or []
    if not components:
        return ["опись пуста: компонентов 0"]
    systems = sorted(
        {str(c.get("name")) for c in components if c.get("type") == "operating-system"}
    )
    problems = [] if os_name in systems else [f"ОС в описи — {systems}, в базе образа — {os_name}"]
    seen = {
        (norm(str(c.get("name"))), str(c.get("version")))
        for c in components
        if str(c.get("purl")).startswith("pkg:pypi/")
    }
    return problems + [f"сканер не видит пакет {n} {v}" for n, v in env if (norm(n), v) not in seen]


def inventory(image: Image, trivy: Trivy, work: Path, runner: Runner) -> bool:
    """Опись одного образа по файлу `docker save` из `run`; окружение — в контейнере без сети.
    Расхождение — `ScanError`; вернуть `False` — образ не красный."""
    archive = work / f"{image.target}.tar"
    if not archive.is_file():
        raise ScanError(f"файла образа {archive} нет — сначала run")
    raw = trivy("image", "--skip-db-update", "--format", "cyclonedx", "--input", str(archive))
    bom = parse(raw, "опись")
    count, digest = len(bom.get("components") or []), hashlib.sha256(raw).hexdigest()
    say(f"{image.target}: опись CycloneDX — компонентов {count}, sha256 {digest}")
    env = python_env(image, runner) if image.python else []
    if problems := bom_problems(bom, env, image.os):
        raise ScanError("; ".join(problems))
    say(f"{image.target}: ОС — {image.os}; пакетов окружения {len(env)} — все в описи")
    return False


def sbom(trivy: str, work: Path, runner: Runner = execute) -> int:
    """A127: опись CycloneDX тем же сканером по тому же файлу образа; число компонентов и sha256
    описи — в журнал; пакеты Python-окружения образа — все в описи с той же версией."""
    scanner = Trivy(trivy, work / "cache", runner)
    return each(lambda image: inventory(image, scanner, work, runner), "описи")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сканер образа: установка Trivy, скан, опись")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("install", help="Trivy файлом выпуска по закреплённой сумме")
    setup.add_argument("--dir", type=Path, required=True, help="каталог установки")
    for name, text in (("run", "канарейка и скан образов списка"), ("sbom", "опись образов")):
        command = commands.add_parser(name, help=text)
        command.add_argument("--work", type=Path, required=True, help="файлы образов и кеш базы")
        command.add_argument("--trivy", default="trivy", help="путь к trivy из install")
    args = parser.parse_args(argv)
    if args.command == "install":
        return install(args.dir)
    return (run if args.command == "run" else sbom)(args.trivy, args.work)


if __name__ == "__main__":
    sys.exit(main())
