"""Разбор `Dockerfile` без Docker (A60, вторая строка; A77, третья): правила — по файлу. Внешние
образы — по дайджесту с линии из списка файла; пакеты — только командой файла: `uv sync` с
`--locked` и `--no-dev` у образа сервиса, `npm ci` у образа web; uv — одна версия в образе и в CI.
Нарушение называет файл и номер строки.
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
WEB_IMAGE = dbtools.ROOT / "deploy" / "web" / "Dockerfile"
UV_IMAGE = "ghcr.io/astral-sh/uv"
#: Правила файла: имя образа и тег линии, команда установки пакетов. Линии — слово владельца
#: 2026-11-19T10:40Z; uv 0.11.26 — решение проверяющего.
RULES = {
    BACKEND_IMAGE: ({"python": "3.12-slim", UV_IMAGE: "0.11.26"}, "uv sync"),
    WEB_IMAGE: ({"node": "24-alpine", "nginx": "1.30-alpine"}, "npm ci"),
}
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


def from_problems(path: Path, lines: dict[str, str]) -> list[str]:
    """A60, A77: внешний образ — в `FROM`, `COPY --from=` и `from=` у `RUN --mount` — только
    `имя:тег линии@sha256:…` из списка файла, иначе источник — стадия выше; `ADD` нет."""
    stages: list[str] = []
    problems = []
    for number, word, args in instructions(path):
        where = f"{path.name}:{number}:"
        if word == "ADD":
            problems.append(f"{where} ADD — файл мимо белого списка или из сети; только COPY")
        sources = re.findall(r"--from=([^\s,]+)", args) if word == "COPY" else []
        mounts = re.findall(r"--mount=(\S+)", args) if word == "RUN" else []
        sources += [m[5:] for mount in mounts for m in mount.split(",") if m.startswith("from=")]
        for source in sources:
            known = source.lower() in stages or (source.isdigit() and int(source) < len(stages))
            pinned = _PINNED.fullmatch(source)
            if not known and not (pinned and lines.get(pinned["name"]) == pinned["tag"]):
                problems.append(f"{where} {word} из {source} — не стадия выше и не образ из списка")
        if word != "FROM":
            continue
        image, *rest = [arg for arg in args.split() if not arg.startswith("--")]
        pinned = _PINNED.fullmatch(image)
        if image.lower() in stages:
            pass
        elif not pinned:
            problems.append(f"{where} FROM {image} — не «имя:тег линии@sha256:…»")
        elif lines.get(pinned["name"]) != pinned["tag"]:
            problems.append(f"{where} FROM {image} — образ не из списка")
        stages.append(rest[1].lower() if len(rest) == 2 and rest[0].upper() == "AS" else "")
    return problems


def install_problems(path: Path, command: str) -> list[str]:
    """A60, A77: пакеты ставит только команда файла; у `uv sync` — `--locked` и `--no-dev`."""
    problems = []
    for number, word, args in instructions(path):
        if word != "RUN":
            continue
        if re.search(r"\bpip\b.*\binstall\b|\buv\s+(pip|add)\b|\bnpm\s+(install|i|add)\b", args):
            problems.append(f"{path.name}:{number}: установка не через {command}")
        for sync in re.findall(r"\buv\s+sync\b[^&;|]*", args):
            missing = [flag for flag in ("--locked", "--no-dev") if flag not in sync.split()]
            if missing:
                problems.append(f"{path.name}:{number}: uv sync без {', '.join(missing)}")
    return problems


def stage_workdir(path: Path) -> str:
    """A60, A71: рабочий каталог итоговой стадии."""
    return [args for _, word, args in instructions(path) if word == "WORKDIR"][-1]


@pytest.mark.parametrize("path", RULES, ids=lambda path: str(path.relative_to(dbtools.ROOT)))
def test_a60_a77_dockerfile_pins_bases_and_installs_by_lock(path: Path) -> None:
    """A60, A77: базы — по дайджесту с линии из списка файла; пакеты — его командой по замку:
    `uv sync --locked --no-dev` у образа сервиса, `npm ci` у образа web."""
    lines, command = RULES[path]
    assert from_problems(path, lines) == []
    assert install_problems(path, command) == []
    assert any(word == "RUN" and command in args for _, word, args in instructions(path))


def test_a60_uv_is_one_version_in_the_image_the_project_and_ci(tmp_path: Path) -> None:
    """A60: uv — одна версия в `Dockerfile`, в файле проекта и в CI. Файлов CI нет — сказано
    словами; CI есть, а версии uv в нём нет, — красное; разные версии — красное со строкой."""
    versions, problems, note = oracle.uv_versions(dbtools.ROOT)
    print(f"A60: uv по местам {versions}; {note or 'CI сверен'}")
    assert problems == []
    assert versions["Dockerfile"] == RULES[BACKEND_IMAGE][0][UV_IMAGE]
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
    ("web: тег без дайджеста", r"(FROM nginx:[\w.-]+)@sha256:\w{64}", r"\1", "не «имя:тег"),
    ("web: npm install", r"RUN npm ci\b", "RUN npm install", "не через npm ci"),
    ("тег без дайджеста", r"(FROM python:3\.12-slim)@sha256:[0-9a-f]{64}", r"\1", "не «имя:тег"),
    ("образ не из списка", r"FROM python:3\.12-slim@", "FROM python:3.13-slim@", "не из списка"),
    ("снят --locked", r"uv sync --locked --no-dev --no-editable", "uv sync --no-dev", "--locked"),
    ("снят --no-dev", r"uv sync --locked --no-dev --no-editable", "uv sync --locked", "--no-dev"),
    ("pip вместо uv", r"uv sync --locked --no-dev --no-editable", "pip install .", "не через uv"),
    (
        "COPY --from образа без дайджеста",
        r"COPY --from=install /opt/venv /opt/venv",
        "COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv",
        "не стадия выше",
    ),
    (
        "RUN --mount from образа без дайджеста",
        r"--mount=from=uv,",
        "--mount=from=ghcr.io/astral-sh/uv:latest,",
        "не стадия выше",
    ),
    (
        "ADD из сети",
        r"(?=COPY backend/alembic\.ini)",
        "ADD https://example.invalid/tool.tar.gz /opt/tool/\n",
        "ADD",
    ),
]


@pytest.mark.parametrize(("spoil", "pattern", "repl", "why"), SPOILS, ids=[s[0] for s in SPOILS])
def test_a60_a77_spoiled_dockerfile_is_red_with_the_line_number(
    tmp_path: Path, spoil: str, pattern: str, repl: str, why: str
) -> None:
    """A60, A77: порча `Dockerfile` — красное с номером первой строки той инструкции, где она
    стоит; порчи web — тег nginx без дайджеста и `npm install` вместо `npm ci`."""
    source = WEB_IMAGE if spoil.startswith("web:") else BACKEND_IMAGE
    (lines, command), text = RULES[source], source.read_text(encoding="utf-8")
    line = text[: re.search(pattern, text).start()].count("\n") + 1
    path = tmp_path / "Dockerfile"
    path.write_text(re.sub(pattern, repl, text, count=1), encoding="utf-8")
    start = max(number for number, _, _ in instructions(path) if number <= line)
    problems = from_problems(path, lines) + install_problems(path, command)
    assert len(problems) == 1
    assert problems[0].startswith(f"Dockerfile:{start}:")
    assert why in problems[0]
