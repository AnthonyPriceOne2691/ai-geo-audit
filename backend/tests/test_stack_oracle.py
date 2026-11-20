"""Чистые части оракула стека без Docker (A61, A62): поиск метки по выгрузке образа, разбор
контекста сборки, сверка списков файлов, имя стека. Модуль грузится по пути файла: оракул лежит
вне пакета и пакет не импортирует.
"""

from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import tarfile
from pathlib import Path

import dbtools
import pytest

sys.path.insert(0, str(dbtools.ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "stack_oracle", dbtools.ROOT / "scripts" / "stack_oracle.py"
)
oracle = importlib.util.module_from_spec(_spec)
sys.modules["stack_oracle"] = oracle
_spec.loader.exec_module(oracle)
compose = importlib.import_module("stack_oracle_compose")

LABEL = "oracle-canary-0f0f0f0f"


def archive(files: dict[str, bytes]) -> io.BytesIO:
    """A61: выгрузка образа в миниатюре — tar с файлами и каталогом."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        directory = tarfile.TarInfo("usr")
        directory.type = tarfile.DIRTYPE
        tar.addfile(directory)
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buffer.seek(0)
    return buffer


def test_a61_label_is_found_in_contents_and_in_names() -> None:
    """A61: метка в содержимом и в имени файла найдена; `.env*` названы; число просмотренных."""
    scan = oracle.scan_tar(
        archive(
            {
                "usr/a.txt": b"clean",
                "app/x.cfg": f"key={LABEL}".encode(),
                f"tmp/{LABEL}.txt": b"clean",
                "app/.env.local": b"clean",
            }
        ),
        LABEL,
    )
    assert scan.scanned == 4
    assert sorted(scan.hits) == ["app/x.cfg", f"tmp/{LABEL}.txt"]
    assert scan.envs == ["app/.env.local"]
    assert len(oracle.label_problems(scan)) == 2


def test_a61_clean_export_passes_and_an_empty_one_is_refused() -> None:
    """A61: без метки и `.env*` — чисто; ноль просмотренных файлов — отказ, а не «чисто»."""
    assert oracle.label_problems(oracle.scan_tar(archive({"usr/a.txt": b"clean"}), LABEL)) == []
    empty = oracle.label_problems(oracle.scan_tar(archive({}), LABEL))
    assert len(empty) == 1 and "0" in empty[0]


def test_a61_scan_reads_only_under_the_prefix() -> None:
    """A61: проба контекста читает только свой каталог в выгрузке."""
    scan = oracle.scan_tar(archive({"ctx/backend/uv.lock": b"x", "usr/a": b"y"}), LABEL, "ctx/")
    assert scan.names == {"backend/uv.lock"} and scan.scanned == 1


OPEN = {
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/src/ai_geo_audit/__init__.py",
    "backend/alembic.ini",
    "backend/migrations/env.py",
}


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (OPEN, 0),
        (OPEN - {"backend/uv.lock"}, 1),
        (OPEN | {"backend/tests/test_x.py"}, 1),
        (OPEN | {"private/notes.md"}, 1),
        (OPEN | {"backend/src/ai_geo_audit/.env.local"}, 1),
    ],
    ids=["ровно пять", "нет пути", "лишний из backend", "личный каталог", "канарейка"],
)
def test_a61_context_has_exactly_the_five_open_paths(names: set[str], expected: int) -> None:
    """A61: в контексте ровно пять открытых путей; лишний путь и канарейка — нарушение."""
    assert len(oracle.context_problems(names)) == expected


def test_a62_file_lists_are_compared_both_ways() -> None:
    """A62: сверка списков называет и пропавшие в образе, и лишние в нём файлы."""
    copy = {"api/app.py", "_oracle_probe.txt"}
    assert oracle.compare("пакет", copy, copy) == []
    problems = oracle.compare("пакет", copy, {"api/app.py", "stray.py"})
    assert problems == [
        "пакет: нет в образе — _oracle_probe.txt",
        "пакет: лишние в образе — stray.py",
    ]


def test_a62_copy_files_skip_python_cache_and_env_canaries(tmp_path: Path) -> None:
    """A62: файлы копии — без кэша Python и без канареек `.env*`, с подложенным файлом данных."""
    for name in ("pkg/a.py", "pkg/__pycache__/a.cpython-312.pyc", "pkg/.env.prod", "pkg/data.txt"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x", encoding="utf-8")
    assert oracle.files_under(tmp_path / "pkg") == {"a.py", "data.txt"}


def test_a61_stack_name_is_the_prefix_and_ten_signs_of_the_path_hash() -> None:
    """A61: имя стека — `ai-geo-audit-oracle-` и 10 знаков sha256 пути копии."""
    name = oracle.stack_name(Path("/work/copy-a"))
    assert name.startswith("ai-geo-audit-oracle-") and len(name) == len("ai-geo-audit-oracle-") + 10
    assert name != oracle.stack_name(Path("/work/copy-b"))
    assert name == oracle.stack_name(Path("/work/copy-a"))


def test_a65_a67_docker_times_and_the_zero_time() -> None:
    """A65, A67: время Docker с наносекундами читается; нулевое время — «не стартовал»."""
    assert compose.parse_time("0001-01-01T00:00:00Z") is None
    early = compose.parse_time("2026-11-20T09:30:01.123456789Z")
    late = compose.parse_time("2026-11-20T09:30:02Z")
    assert early is not None and late is not None
    assert early < late and early.microsecond == 123456


def test_a63_process_users_are_read_under_the_top_header() -> None:
    """A63: пользователи процессов — столбец UID под заголовком `docker compose top`."""
    head = "stack-api-1\nUID    PID   PPID  C  STIME  TTY  TIME  CMD\n"
    rows = (
        "10001  101   1     0  09:30  ?    0:00  tini\n10001  102   101   0  09:30  ?    0:01  py\n"
    )
    assert compose.top_uids(head + rows) == {"10001"}
    assert compose.top_uids(head + rows.replace("10001  102", "root   102")) == {"10001", "root"}


def test_a63_process_users_in_the_compose_5_top_layout() -> None:
    """A63: у Compose 5.x (замер 5.7.0) первые столбцы `top` — SERVICE и #, UID — третий."""
    text = (
        "SERVICE  #   UID    PID    PPID   C   STIME  TTY  TIME      CMD\n"
        "api      1   10001  81294  81270  0   09:38  ?    00:00:00  /sbin/docker-init -- python\n"
        "api      1   10001  81317  81294  35  09:38  ?    00:00:00  python -m ai_geo_audit.api\n"
    )
    assert compose.top_uids(text) == {"10001"}


class LeftBehind(oracle.Docker):
    """Подставной исполнитель: уборка прошла, а по меткам compose остались контейнер и том."""

    def result(self, *args: str, limit: str = "run", **kw: object) -> subprocess.CompletedProcess:
        left = {("container", "ls"): b"c0ffee000001\n", ("volume", "ls"): b"oracle_db-data\n"}
        return subprocess.CompletedProcess(["docker", *args], 0, left.get(args[:2], b""), b"")


def test_a65_left_behind_container_or_volume_makes_the_run_red(tmp_path: Path) -> None:
    """A65: своё, оставшееся после уборки, — красное и код не ноль, даже если шаги зелёные."""
    run = oracle.Run(LeftBehind(), tmp_path / "copy", "ai-geo-audit-oracle-test", LABEL, {})
    left = run.cleanup()
    assert left == ["container c0ffee000001", "volume oracle_db-data"]
    code, line = oracle.verdict(0, left)
    assert code == 1 and "КРАСНОЕ" in line and "oracle_db-data" in line
    assert oracle.verdict(0, []) == (0, f"итог: шагов {len(oracle.STEPS)}, красных 0")
