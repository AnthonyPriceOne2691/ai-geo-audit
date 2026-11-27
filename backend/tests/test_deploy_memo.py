"""Памятка подготовки сервера `deploy/README.md` (6а-3, срез 7): сказанное в ней — правда скрипта
выката. Команды — команды `deploy.py`, имена владельца — блока «сервер» образца, корни — ядра,
пороги ресурсов — `deploy.py check`; запуск — `python3 -I`, долгие команды — `tmux new -d`."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from deploy_fakes import ROOT, tool

MEMO = ROOT / "deploy" / "README.md"


def test_a185_memo_commands_are_the_script_commands() -> None:
    """A185: каждая команда скрипта — в памятке строкой запуска `python3 -I deploy/deploy.py`, и
    других там нет; долгие — под `tmux new -d`, ход — `last`."""
    text = MEMO.read_text(encoding="utf-8")
    assert set(re.findall(r"python3 -I deploy/deploy\.py ([a-z-]+)", text)) == set(tool.COMMANDS)
    assert "`tmux new -d`" in text and "`last`" in text


def test_a185_memo_owner_names_roots_and_floors_are_the_script_ones() -> None:
    """A185, A182: имена владельца — ровно «задаёт владелец» блока «сервер» образца, по порядку;
    пути таблицы — корни и файлы ядра, владелец и права — умолчания функции файлов и контекста;
    пороги памяти и места — те, что судит `deploy.py check`."""
    text = MEMO.read_text(encoding="utf-8")
    sample = (ROOT / ".env.example").read_text(encoding="utf-8")
    block = sample.split("## задаёт владелец\n", 1)[1].split("## ", 1)[0]
    owner = [line[2:].split("=", 1)[0] for line in block.splitlines() if line.startswith("# ")]
    assert ", ".join(f"`{name}`" for name in owner) in " ".join(text.split())
    paths = tool.Paths()
    roots = [paths.service, paths.env_file, paths.deploy_dir, paths.backups, paths.etc]
    roots += [paths.master_key, paths.incoming]
    rows = re.findall(r"^\| `([^`]+)` \| ([^|]+) \| ([^|]+) \|", text, re.MULTILINE)
    assert [row[0].rstrip("/") for row in rows] == [str(path) for path in roots]
    files = (tool.Files.write, tool.Files.mkdir)
    write, mkdir = (inspect.signature(f).parameters["mode"].default for f in files)
    root = {0: "root"}[tool.Context.__dataclass_fields__["owner"].default]
    want = {paths.env_file: (root, f"{write:o}")}
    want |= dict.fromkeys((paths.deploy_dir, paths.etc, paths.incoming), (root, f"{mkdir:o}"))
    assert {Path(row[0]): row[1:] for row in rows if Path(row[0]) in want} == want
    assert f"| `{paths.master_key}` | uid {tool.API_UID} | 400 |" in text
    floors = (tool.MEMORY_FLOOR, tool.DISK_FLOOR)
    assert all(f"не меньше {floor} ГБ" in text for floor in floors)
