"""Разбор `Dockerfile` без Docker (A60, вторая строка): внешние образы — по дайджесту с линии из
списка, зависимости — только `uv sync` с `--locked` и `--no-dev`, uv — одна версия в образе и в
CI. Нарушение называет файл и номер строки. Ч1.5б добавит свой `Dockerfile` в `DOCKERFILES`.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from pathlib import Path

import dbtools
import pytest

from ai_geo_audit.ops import stack_rules

sys.path.insert(0, str(dbtools.ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "stack_oracle", dbtools.ROOT / "scripts/stack_oracle.py"
)
oracle = importlib.util.module_from_spec(_spec)
sys.modules["stack_oracle"] = oracle
_spec.loader.exec_module(oracle)

BACKEND_IMAGE = dbtools.ROOT / "deploy" / "backend" / "Dockerfile"
DOCKERFILES = [BACKEND_IMAGE]
#: Имя образа и тег линии (слово владельца 2026-11-19T10:40Z; uv 0.11.26 — решение проверяющего).
LINES = {"python": "3.12-slim", "ghcr.io/astral-sh/uv": "0.11.26"}
UV_IMAGE = "ghcr.io/astral-sh/uv"
_PINNED = re.compile(r"(?P<name>[^:@\s]+):(?P<tag>[^@\s]+)@sha256:[0-9a-f]{64}")


def instructions(path: Path) -> list[tuple[int, str, str]]:
    """A60: инструкции — номер первой строки, слово, аргументы; продолжения `\\` склеены."""
    found, start, parts = [], 0, []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        start = start if parts else number
        parts.append(text.removesuffix("\\").strip())
        if not text.endswith("\\"):
            word, _, args = " ".join(parts).partition(" ")
            found.append((start, word.upper(), args.strip()))
            parts = []
    return found


def from_problems(path: Path) -> list[str]:
    """A60: каждая строка `FROM` внешнего образа — `имя:тег линии@sha256:…` из списка."""
    stages: set[str] = set()
    problems = []
    for number, word, args in instructions(path):
        if word != "FROM":
            continue
        image, *rest = [arg for arg in args.split() if not arg.startswith("--")]
        pinned = _PINNED.fullmatch(image)
        if image.lower() in stages:
            pass
        elif not pinned:
            problems.append(f"{path.name}:{number}: FROM {image} — не «имя:тег линии@sha256:…»")
        elif LINES.get(pinned["name"]) != pinned["tag"]:
            problems.append(f"{path.name}:{number}: FROM {image} — образ не из списка")
        if len(rest) == 2 and rest[0].upper() == "AS":
            stages.add(rest[1].lower())
    return problems


def install_problems(path: Path) -> list[str]:
    """A60: зависимости ставит только `uv sync`, и у каждого — `--locked` и `--no-dev`."""
    problems = []
    for number, word, args in instructions(path):
        if word != "RUN":
            continue
        if re.search(r"\bpip\b.*\binstall\b|\buv\s+(pip|add)\b", args):
            problems.append(f"{path.name}:{number}: установка не через uv sync")
        for sync in re.findall(r"\buv\s+sync\b[^&;|]*", args):
            missing = [flag for flag in ("--locked", "--no-dev") if flag not in sync.split()]
            if missing:
                problems.append(f"{path.name}:{number}: uv sync без {', '.join(missing)}")
    return problems


def stage_workdir(path: Path) -> str:
    """A60, A71: рабочий каталог итоговой стадии."""
    return [args for _, word, args in instructions(path) if word == "WORKDIR"][-1]


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda path: str(path.relative_to(dbtools.ROOT)))
def test_a60_dockerfile_pins_bases_and_installs_by_lock(path: Path) -> None:
    """A60: базы — по дайджесту с линии из списка; установка — `uv sync --locked --no-dev`."""
    assert from_problems(path) == []
    assert install_problems(path) == []
    assert any(word == "RUN" and "uv sync" in args for _, word, args in instructions(path))


def test_a60_uv_is_one_version_in_the_image_the_project_and_ci(tmp_path: Path) -> None:
    """A60: uv — одна версия в `Dockerfile`, в файле проекта и в CI. Файлов CI нет — сказано
    словами; CI есть, а версии uv в нём нет, — красное; разные версии — красное со строкой."""
    versions, problems, note = oracle.uv_versions(dbtools.ROOT)
    print(f"A60: uv по местам {versions}; {note or 'CI сверен'}")
    assert problems == []
    assert versions["Dockerfile"] == LINES[UV_IMAGE]
    assert any(place.startswith("pyproject.toml:") for place in versions)
    root = tmp_path
    (root / "deploy" / "backend").mkdir(parents=True)
    shutil.copy(BACKEND_IMAGE, root / "deploy" / "backend" / "Dockerfile")
    (root / "backend").mkdir()
    shutil.copy(dbtools.BACKEND / "pyproject.toml", root / "backend" / "pyproject.toml")
    assert oracle.uv_versions(root)[1:] == ([], "файлов CI нет — сверять uv с CI не с чем")
    (root / ".github" / "workflows").mkdir(parents=True)
    ci = root / ".github" / "workflows" / "quality.yml"
    ci.write_text("steps:\n  - run: python -m pip install uv==0.11.26\n", encoding="utf-8")
    assert oracle.uv_versions(root)[1:] == ([], "")
    ci.write_text("steps:\n  - run: python -m pip install uv==0.11.25\n", encoding="utf-8")
    [mismatch] = oracle.uv_versions(root)[1]
    assert "quality.yml:2" in mismatch and "0.11.25" in mismatch
    ci.write_text("steps:\n  - run: python -m pip install ruff\n", encoding="utf-8")
    assert oracle.uv_versions(root)[1] == ["CI есть, а версии uv в нём нет"]


def test_a60_a71_image_workdir_is_the_one_the_compose_rules_guard() -> None:
    """A60, A71: рабочий каталог образа — тот, где правило `workdir-volume` запрещает том."""
    assert stage_workdir(BACKEND_IMAGE) == stack_rules.IMAGE_WORKDIR


SPOILS = [
    ("тег без дайджеста", r"(FROM python:3\.12-slim)@sha256:[0-9a-f]{64}", r"\1", "не «имя:тег"),
    ("образ не из списка", r"FROM python:3\.12-slim@", "FROM python:3.13-slim@", "не из списка"),
    ("снят --locked", r"uv sync --locked --no-dev --no-editable", "uv sync --no-dev", "--locked"),
    ("снят --no-dev", r"uv sync --locked --no-dev --no-editable", "uv sync --locked", "--no-dev"),
    ("pip вместо uv", r"uv sync --locked --no-dev --no-editable", "pip install .", "не через uv"),
]


@pytest.mark.parametrize(("spoil", "pattern", "repl", "why"), SPOILS, ids=[s[0] for s in SPOILS])
def test_a60_spoiled_dockerfile_is_red_with_the_line_number(
    tmp_path: Path, spoil: str, pattern: str, repl: str, why: str
) -> None:
    """A60: порча `Dockerfile` — красное с номером первой строки той инструкции, где она стоит."""
    text = BACKEND_IMAGE.read_text(encoding="utf-8")
    line = text[: re.search(pattern, text).start()].count("\n") + 1
    path = tmp_path / "Dockerfile"
    path.write_text(re.sub(pattern, repl, text, count=1), encoding="utf-8")
    start = max(number for number, _, _ in instructions(path) if number <= line)
    problems = from_problems(path) + install_problems(path)
    assert len(problems) == 1
    assert problems[0].startswith(f"Dockerfile:{start}:")
    assert why in problems[0]
