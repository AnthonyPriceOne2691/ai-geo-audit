"""Сканер образа без Docker и без сети (A124–A129): установка Trivy на подставных архивах
`file://`, разбор вывода подставного `trivy`, страж списка скана, опись, исключения, флаги.

Модуль `scripts/image_scan.py` лежит вне пакета и грузится по пути файла. Исполнитель команд
подставной: отвечает за `trivy` и `docker` по виду команды и пишет каждую команду. Вид ответов —
отчёт Trivy JSON (`Trivy.Version`, `Results[].Packages`, `Results[].Vulnerabilities`), опись
CycloneDX и метаданные базы `<cache-dir>/db/metadata.json` — по исходникам Trivy на метке v0.76.2.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import dbtools
import pytest

_spec = importlib.util.spec_from_file_location(
    "image_scan", dbtools.ROOT / "scripts" / "image_scan.py"
)
assert _spec is not None
assert _spec.loader is not None
scan = importlib.util.module_from_spec(_spec)
sys.modules["image_scan"] = scan
_spec.loader.exec_module(scan)

ROOT = dbtools.ROOT
VERSION = "0.76.2"
URL = (
    "https://github.com/aquasecurity/trivy/releases/download/v0.76.2/"
    "trivy_0.76.2_Linux-64bit.tar.gz"
)
SHA256 = "bc0fc726e017b8927a62b2d3531d7a84ee01769daf5c99ed866dced54089fdbf"
BINARY = b"#!/bin/sh\necho trivy\n"
#: Метаданные базы — как их пишет `--download-db-only` в `<cache-dir>/db/metadata.json`.
META = {
    "Version": 2,
    "NextUpdate": "2026-11-21T06:12:03.415196862Z",
    "UpdatedAt": "2026-11-20T06:12:03.415196862Z",
    "DownloadedAt": "2026-11-20T10:01:02.5Z",
}
#: Окружение образа — имена, как их даёт `importlib.metadata`. В описи имя то же, что в METADATA
#: (`PyYAML`), или уже по PEP 503 (`typing-extensions`): сверка приводит обе стороны.
ENV = [("fastapi", "1.0.0"), ("PyYAML", "6.0.3"), ("typing_extensions", "4.15.0")]
BOM = {
    "bomFormat": "CycloneDX",
    "components": [
        {"type": "operating-system", "name": "debian", "version": "13.2"},
        {"type": "library", "name": "libc6", "version": "2.41-12", "purl": "pkg:deb/debian/libc6"},
        {
            "type": "library",
            "name": "fastapi",
            "version": "1.0.0",
            "purl": "pkg:pypi/fastapi@1.0.0",
        },
        {"type": "library", "name": "PyYAML", "version": "6.0.3", "purl": "pkg:pypi/pyyaml@6.0.3"},
        {
            "type": "library",
            "name": "typing-extensions",
            "version": "4.15.0",
            "purl": "pkg:pypi/typing-extensions@4.15.0",
        },
    ],
}


def vuln(ident: str, package: str, version: str, fixed: str = "", severity: str = "HIGH") -> Any:
    """Находка отчёта Trivy JSON: `Results[].Vulnerabilities[]`."""
    return {
        "VulnerabilityID": ident,
        "PkgName": package,
        "InstalledVersion": version,
        "FixedVersion": fixed,
        "Severity": severity,
    }


CANARY = vuln("CVE-2020-14343", "PyYAML", "5.3", "5.4", "CRITICAL")


def report(*found: Any, packages: int = 3, version: str = VERSION) -> bytes:
    """Отчёт Trivy JSON: версия сканера, пакеты и находки одной цели."""
    result: dict[str, Any] = {"Target": "x", "Class": "lang-pkgs", "Type": "python-pkg"}
    result["Packages"] = [{"Name": f"p{number}"} for number in range(packages)]
    if found:
        result["Vulnerabilities"] = list(found)
    return json.dumps(
        {"SchemaVersion": 2, "Trivy": {"Version": version}, "Results": [result]}
    ).encode()


class Fake:
    """Подставной исполнитель: `trivy` и `docker` по виду команды; каждая команда записана."""

    def __init__(
        self,
        image: bytes | None = None,
        canary: bytes | None = None,
        *,
        db: bool = True,
        env: list[tuple[str, str]] | None = None,
    ) -> None:
        self.image = report() if image is None else image
        self.canary = report(CANARY) if canary is None else canary
        self.db = db
        self.env = ENV if env is None else env
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], limit: int) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(argv))
        code, out, err = 0, self.image, b""
        if argv[0] == "docker":
            if argv[1] == "save":
                Path(argv[argv.index("--output") + 1]).write_bytes(b"image")
            out = json.dumps(self.env).encode() if argv[1] == "run" else b""
        elif "--download-db-only" in argv and not self.db:
            code, out = 1, b""
            err = b"FATAL\tinit error: DB error: failed to download vulnerability DB"
        elif "--download-db-only" in argv:
            db = Path(argv[argv.index("--cache-dir") + 1]) / "db"
            db.mkdir(parents=True)
            (db / "metadata.json").write_text(json.dumps(META), encoding="utf-8")
            out = b""
        elif argv[1] == "rootfs":
            out = self.canary
        elif "cyclonedx" in argv:
            out = json.dumps(BOM).encode()
        return subprocess.CompletedProcess(argv, code, out, err)


def tarball(path: Path, members: dict[str, bytes | str]) -> Path:
    """Архив выпуска в миниатюре: байты — обычный файл, строка — ссылка на файл архива."""
    with tarfile.open(path, "w:gz") as tar:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            if isinstance(body, str):
                info.type, info.linkname = tarfile.SYMTYPE, body
                tar.addfile(info)
            else:
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
    return path


def release(archive: Path, sha256: str | None = None) -> Any:
    """Запись выпуска для подставного архива по адресу `file://`; сумма — его, если не задана."""
    digest = sha256 or hashlib.sha256(archive.read_bytes()).hexdigest()
    return scan.Release(VERSION, archive.as_uri(), digest)


def test_a124_release_is_one_record_with_its_version_in_the_url() -> None:
    """A124, первая строка: версия 0.76.2, адрес выпуска и sha256 — одна запись в скрипте; версия
    в адресе равна версии записи. Порча «чужая версия в адресе» — красное."""
    assert (scan.RELEASE.version, scan.RELEASE.url, scan.RELEASE.sha256) == (VERSION, URL, SHA256)
    base = re.escape("https://github.com/aquasecurity/trivy/releases/download/")
    found = re.fullmatch(base + r"v([^/]+)/trivy_([^_]+)_Linux-64bit\.tar\.gz", scan.RELEASE.url)
    assert found is not None
    assert found[1] == found[2] == scan.RELEASE.version


def test_a124_sum_is_written_once_in_the_repository() -> None:
    """A124, первая строка: sha256 выпуска записана один раз — в скрипте сканера; другие скрипты и
    файлы CI её не повторяют."""
    places = [
        f"{path.relative_to(ROOT)}:{number}"
        for base in ("scripts", ".github")
        for path in sorted((ROOT / base).rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
        for number, line in enumerate(path.read_text("utf-8", "replace").splitlines(), 1)
        if SHA256 in line
    ]
    assert len(places) == 1, places
    assert places[0].startswith("scripts/image_scan.py:"), places


def aquasecurity_uses(root: Path) -> list[str]:
    """Файлы workflow, где `uses:` берёт действие `aquasecurity/` (A124)."""
    uses = re.compile(r"^\s*(-\s+)?uses:\s*[\"']?aquasecurity/", re.MULTILINE)
    files = sorted((root / ".github" / "workflows").glob("*.y*ml"))
    return [path.name for path in files if uses.search(path.read_text(encoding="utf-8"))]


def test_a124_no_workflow_takes_an_aquasecurity_action(tmp_path: Path) -> None:
    """A124, первая строка: ни в одном workflow нет `uses:` с `aquasecurity/`. Проба во временной
    копии — `uses: aquasecurity/setup-trivy` — красное с именем файла. Файлов workflow в ветке
    может не быть: тогда сверять нечего."""
    assert aquasecurity_uses(ROOT) == []
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    step = "      - uses: aquasecurity/setup-trivy@v0.2.6\n"
    (workflows / "scan.yml").write_text(f"jobs:\n  scan:\n    steps:\n{step}", encoding="utf-8")
    assert aquasecurity_uses(tmp_path) == ["scan.yml"]


def test_a124_install_takes_only_the_regular_trivy_file(tmp_path: Path) -> None:
    """A124, вторая строка: сумма сошлась — из архива только обычный файл `trivy`, права 755."""
    members: dict[str, bytes | str] = {"LICENSE": b"x", "contrib/html.tpl": b"y", "trivy": BINARY}
    target = tmp_path / "bin"
    assert scan.install(target, release(tarball(tmp_path / "ok.tar.gz", members))) == 0
    assert [path.name for path in target.iterdir()] == ["trivy"]
    assert (target / "trivy").read_bytes() == BINARY
    assert stat.S_IMODE((target / "trivy").stat().st_mode) == 0o755


@pytest.mark.parametrize(
    ("members", "wrong_sum", "reason"),
    [
        ({"trivy": BINARY}, True, "sha256 не совпала"),
        ({"README.md": b"x", "trivy": "README.md"}, False, "trivy — не обычный файл"),
        ({"README.md": b"x"}, False, "trivy нет в архиве"),
    ],
    ids=["сумма не та", "trivy — ссылка", "trivy нет"],
)
def test_a124_install_refuses_and_leaves_the_directory_empty(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    members: dict[str, bytes | str],
    wrong_sum: bool,
    reason: str,
) -> None:
    """A124, вторая строка: отказ, код ≠ 0, причина в выводе; каталог установки пуст. Архив с
    чужой суммой не открывается вовсе: порча «распаковка до сверки суммы» — красное."""
    archive = tarball(tmp_path / "release.tar.gz", members)
    opened: list[Any] = []
    real_open = tarfile.open

    def spy(*args: Any, **kwargs: Any) -> Any:
        opened.append(args)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(scan.tarfile, "open", spy)
    target = tmp_path / "bin"
    target.mkdir()
    code = scan.install(target, release(archive, "0" * 64 if wrong_sum else None))
    assert code != 0
    assert reason in capsys.readouterr().out
    assert list(target.iterdir()) == []
    assert not (wrong_sum and opened), "архив открыт до сверки суммы"


def test_a124_install_broken_mid_copy_leaves_the_directory_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A124, вторая строка: сумма сошлась, но запись `trivy` оборвалась (диск полон) — отказ,
    код 1, причина в выводе; недописанного файла в каталоге установки нет."""
    archive = tarball(tmp_path / "release.tar.gz", {"trivy": b"#!/bin/sh\n" * 1000})

    def broken(source: Any, out: Any, *args: Any) -> None:
        out.write(source.read(64))
        raise OSError("диск полон")

    monkeypatch.setattr(scan.shutil, "copyfileobj", broken)
    target = tmp_path / "bin"
    target.mkdir()
    assert scan.install(target, release(archive)) == 1
    assert "диск полон" in capsys.readouterr().out
    assert list(target.iterdir()) == []


def test_a125_a126_canary_is_found_and_the_journal_names_scanner_db_and_packages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A125; A126, первая строка: канарейка CVE-2020-14343 у PyYAML 5.3 найдена; в журнале —
    версия сканера, версия и время сборки базы, число пакетов; находок нет — код 0. Канарейка —
    каталог с `site-packages/PyYAML-5.3.dist-info/METADATA`."""
    fake = Fake()
    assert scan.run("trivy", tmp_path, fake) == 0
    out = capsys.readouterr().out
    journal = ("Trivy 0.76.2", "CVE-2020-14343", "база уязвимостей v2", "пакетов 3")
    for text in (*journal, "собрана 2026-11-20T06:12:03Z", "получена 2026-11-20T10:01:02Z"):
        assert text in out, text
    rootfs = next(call for call in fake.calls if call[1] == "rootfs")
    metadata = Path(rootfs[-1]) / "site-packages" / "PyYAML-5.3.dist-info" / "METADATA"
    assert "Name: PyYAML\nVersion: 5.3\n" in metadata.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("canary", "reason"),
    [
        (report(), "сканер не видит известную уязвимость"),
        (report({**CANARY, "FixedVersion": ""}), "сканер не видит известную уязвимость"),
        (report(CANARY, version="0.69.4"), "сканер не той версии"),
    ],
    ids=["находки нет", "без исправления 5.4", "версия 0.69.4"],
)
def test_a125_blind_or_foreign_scanner_is_red_before_the_images(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], canary: bytes, reason: str
) -> None:
    """A125: подставной `trivy` без находки канарейки или без её исправления 5.4 — красное
    «сканер не видит известную уязвимость»; сканер не той версии (0.69.4 — подменённый выпуск
    2026 года) — красное. Образ после этого не собирается и не сканируется."""
    fake = Fake(canary=canary)
    assert scan.run("trivy", tmp_path, fake) == 1
    assert reason in capsys.readouterr().out
    assert [call for call in fake.calls if call[0] == "docker" or "--input" in call] == []


def test_a126_fixed_findings_are_a_table_and_code_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A126, первая строка: находки CRITICAL и HIGH с исправлением — таблица (идентификатор,
    пакет, версия, версия с исправлением) и код 1; без исправления — отдельным списком; ниже HIGH
    — не в счёт. Порча «порог без HIGH» — красное."""
    image = report(
        vuln("CVE-2026-0001", "libssl3", "3.5.1-1", "3.5.1-2", "CRITICAL"),
        vuln("CVE-2026-0002", "starlette", "0.47.0", "0.47.2", "HIGH"),
        vuln("CVE-2026-0003", "zlib1g", "1:1.3.dfsg-3", "", "HIGH"),
        vuln("CVE-2026-0004", "curl", "8.14.1-1", "8.14.1-2", "MEDIUM"),
    )
    assert scan.run("trivy", tmp_path, Fake(image=image)) == 1
    out = capsys.readouterr().out
    assert "| идентификатор | пакет | версия | исправление |" in out
    assert "| CVE-2026-0001 | libssl3 | 3.5.1-1 | 3.5.1-2 |" in out
    assert "| CVE-2026-0002 | starlette | 0.47.0 | 0.47.2 |" in out
    assert "без исправления — CVE-2026-0003 zlib1g 1:1.3.dfsg-3 (HIGH)" in out
    assert "CVE-2026-0004" not in out


def test_a126_findings_without_a_fix_are_listed_and_do_not_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A126, первая строка: только находки без исправления — список, джоба не краснеет, код 0
    (решение владельца)."""
    image = report(vuln("CVE-2026-0003", "zlib1g", "1:1.3.dfsg-3", "", "CRITICAL"))
    assert scan.run("trivy", tmp_path, Fake(image=image)) == 0
    out = capsys.readouterr().out
    assert "без исправления — CVE-2026-0003 zlib1g" in out
    assert "| CVE-2026-0003" not in out


def test_a126_db_download_failure_is_red_and_not_zero_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A126, вторая строка: подставной `trivy` выходит с отказом загрузки базы — красное «база
    уязвимостей не получена» с причиной сканера, а не «находок нет»; сканов после отказа нет."""
    fake = Fake(db=False)
    assert scan.run("trivy", tmp_path, fake) == 1
    out = capsys.readouterr().out
    assert "база уязвимостей не получена" in out
    assert "failed to download vulnerability DB" in out
    assert "пакетов" not in out
    assert all("--download-db-only" in call for call in fake.calls)


@pytest.mark.parametrize(
    "image", [b"", b" \n", report(packages=0)], ids=["пустой вывод", "пробелы", "пакетов 0"]
)
def test_a126_empty_scan_is_not_zero_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], image: bytes
) -> None:
    """A126, вторая строка: скан вышел с кодом 0, а отчёта нет или в нём ноль пакетов — красное,
    а не «находок нет»."""
    assert scan.run("trivy", tmp_path, Fake(image=image)) == 1
    out = capsys.readouterr().out
    assert "backend: КРАСНОЕ" in out
    assert "с исправлением 0" not in out


def test_a126_every_dockerfile_in_deploy_is_in_the_scan_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A126, третья строка: каждый `Dockerfile` в `deploy/` на любой глубине — в списке скана.
    Проба во временной копии — лишний `deploy/probe/Dockerfile` — красное с именем файла; образ
    вне списка краснит `run` до первого вызова сканера."""
    assert scan.guard_problems(ROOT) == []
    shutil.copytree(ROOT / "deploy", tmp_path / "deploy")
    (tmp_path / "deploy" / "probe").mkdir()
    (tmp_path / "deploy" / "probe" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    assert scan.guard_problems(tmp_path) == [
        "deploy/probe/Dockerfile — Dockerfile вне списка скана"
    ]
    monkeypatch.setattr(scan, "IMAGES", ())
    fake = Fake()
    assert scan.run("trivy", tmp_path / "work", fake) == 1
    assert "deploy/backend/Dockerfile — Dockerfile вне списка скана" in capsys.readouterr().out
    assert fake.calls == []


@pytest.mark.parametrize(
    ("env", "code", "text"),
    [
        (ENV, 0, "пакетов окружения 3 — все в описи"),
        ([*ENV, ("httpx", "0.28.1")], 1, "сканер не видит пакет httpx 0.28.1"),
    ],
    ids=["окружение в описи", "пакет без компонента"],
)
def test_a127_bom_of_the_same_image_file_and_its_count_and_sum_in_the_journal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    env: list[tuple[str, str]],
    code: int,
    text: str,
) -> None:
    """A127: опись CycloneDX того же файла образа; каждый пакет окружения — в описи с той же
    версией (имена по PEP 503), иначе «сканер не видит пакет <имя>»; число компонентов и sha256
    описи — в журнале; окружение читается в контейнере без сети."""
    fake = Fake(env=env)
    assert scan.run("trivy", tmp_path, fake) == 0
    capsys.readouterr()
    assert scan.sbom("trivy", tmp_path, fake) == code
    out = capsys.readouterr().out
    digest = hashlib.sha256(json.dumps(BOM).encode()).hexdigest()
    assert f"компонентов {len(BOM['components'])}, sha256 {digest}" in out
    assert text in out
    bom = next(call for call in fake.calls if "cyclonedx" in call)
    assert bom[bom.index("--input") + 1] == str(tmp_path / "backend.tar")
    probe = next(call for call in fake.calls if call[:2] == ["docker", "run"])
    assert probe[probe.index("--network") + 1] == "none"


def test_a127_bom_problems_name_the_package_the_empty_bom_and_the_os() -> None:
    """A127: пакет без компонента или с другой версией — «сканер не видит пакет <имя>»; пакет ОС
    под тем же именем компонентом Python не считается; пустая опись — отказ; ОС не та, что в базе
    образа, — красное."""
    assert scan.bom_problems(BOM, ENV, "debian") == []
    other = [("fastapi", "9.9.9"), ("libc6", "2.41-12")]
    expected = ["сканер не видит пакет fastapi 9.9.9", "сканер не видит пакет libc6 2.41-12"]
    assert scan.bom_problems(BOM, other, "debian") == expected
    assert scan.bom_problems({"components": []}, ENV, "debian") == ["опись пуста: компонентов 0"]
    assert [problem[:9] for problem in scan.bom_problems(BOM, ENV, "alpine")] == ["ОС в опис"]


def test_a127_sbom_without_the_image_file_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A127: без файла образа из `run` описи нет — отказ, код 1; разбор аргументов — тот же, что
    у джобы."""
    assert scan.main(["sbom", "--work", str(tmp_path), "--trivy", "trivy"]) == 1
    assert "сначала run" in capsys.readouterr().out


GOOD = "# PyYAML без выпуска с исправлением; by=human:owner at=2026-11-20T12:00Z\n"
GOOD += "CVE-2026-0001 exp:2026-12-31\n"


def test_a128_repository_trivyignore_follows_the_line_rule() -> None:
    """A128: в `.trivyignore` репозитория у каждой строки находки — срок `exp:ГГГГ-ММ-ДД` и над
    ней свой комментарий с причиной и `by=human:owner`; образец по правилу проходит."""
    assert scan.ignore_problems((ROOT / ".trivyignore").read_text(encoding="utf-8")) == []
    assert scan.ignore_problems(GOOD) == []


@pytest.mark.parametrize(
    ("text", "number"),
    [
        (GOOD.replace(" exp:2026-12-31", ""), 2),
        (GOOD.replace("exp:2026-12-31", "exp:2026-13-01"), 2),
        (GOOD.replace("; by=human:owner", ""), 2),
        ("# by=human:owner at=2026-11-20T12:00Z\nCVE-2026-0001 exp:2026-12-31\n", 2),
        (GOOD + "CVE-2026-0002 exp:2026-12-31\n", 3),
        ("# шапка\n\nCVE-2026-0001 exp:2026-12-31\n", 3),
    ],
    ids=[
        "без срока",
        "срок не дата",
        "без by=human:owner",
        "без причины",
        "под чужим комментарием",
        "без комментария",
    ],
)
def test_a128_bad_exception_line_is_red_with_its_number(text: str, number: int) -> None:
    """A128: строка находки без срока, без `by=human:owner`, без причины или без своего
    комментария над ней — красное с номером строки."""
    problems = scan.ignore_problems(text)
    assert problems, text
    assert all(problem.startswith(f".trivyignore:{number}:") for problem in problems), problems


def test_a128_canary_runs_without_the_ignore_file(tmp_path: Path) -> None:
    """A128: канарейка идёт с `--ignorefile ""` — исключения её не скрывают; скан образа — с
    файлом исключений репозитория."""
    fake = Fake()
    assert scan.run("trivy", tmp_path, fake) == 0
    rootfs = next(call for call in fake.calls if call[1] == "rootfs")
    image = next(call for call in fake.calls if "--input" in call and "json" in call)
    assert rootfs[rootfs.index("--ignorefile") + 1] == ""
    assert image[image.index("--ignorefile") + 1] == str(ROOT / ".trivyignore")


def trivy_calls(work: Path) -> list[list[str]]:
    """Все вызовы `trivy` за `run` и `sbom`: база, канарейка, скан образа, опись."""
    fake = Fake()
    assert scan.run("trivy", work, fake) == 0
    assert scan.sbom("trivy", work, fake) == 0
    calls = [call for call in fake.calls if call[0] == "trivy"]
    assert len(calls) == 4, calls
    return calls


@pytest.mark.parametrize("flag", ["--skip-version-check", "--disable-telemetry"])
def test_a129_every_trivy_call_has_the_flag(tmp_path: Path, flag: str) -> None:
    """A129: у каждого вызова `trivy` — без проверки версии и без телеметрии: наружу он ходит
    только за базой. Снятый флаг — красное с его именем."""
    missing = [call[:2] for call in trivy_calls(tmp_path) if flag not in call]
    assert not missing, f"{flag} нет у вызовов {missing}"


def test_a129_every_scan_has_scanners_vuln(tmp_path: Path) -> None:
    """A129: у каждого скана — `--scanners vuln`: секреты сканер не ищет. Снятый флаг — красное
    с его именем."""
    wrong = [
        call[:2]
        for call in trivy_calls(tmp_path)
        if "--scanners" not in call or call[call.index("--scanners") + 1] != "vuln"
    ]
    assert not wrong, f"--scanners vuln нет у вызовов {wrong}"
