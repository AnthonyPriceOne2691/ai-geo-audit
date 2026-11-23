"""A289: доли мутационного гейта у python-половины и сумма долей — настоящими скриптами гейта.

Доля выбирает файлы `ts_shard` канона и только считает; порог 60 % судит сумма счёта всех долей;
доля без счёта — сумма «не судит» с номером доли; доле без файлов — счёт «0 0», сумма не красная.
mutmut здесь не запускается: доля с файлами проверяется до инструментов, в PATH гейта mutmut нет.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
from pathlib import Path

import envguard
import pytest

LINT = envguard.ROOT / "scripts" / "lint"
PARTS = (
    "check_mutation_gate.sh",
    "mutation_ts.sh",
    "mutation_shard.sh",
    "mutation_py.sh",
    "mutation_verdict.sh",
)
#: PATH гейта без каталогов с mutmut: доля с файлами доходит до проверки инструментов, не дальше.
PATH = os.pathsep.join(
    part
    for part in os.environ.get("PATH", "").split(os.pathsep)
    if part and not (Path(part) / "mutmut").exists()
)
#: Окружение git проб без `GIT_*`: под хуком git они смотрели бы в репозиторий проекта.
GIT_ENV = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def gate(lint: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Вход гейта отдельным процессом с окружением с нуля: PATH без mutmut и заданное."""
    return subprocess.run(
        ["bash", str(lint / "check_mutation_gate.sh")],
        env={"PATH": PATH, "HOME": os.environ.get("HOME", ""), **env},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def bash(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["bash", "-c", script, "probe", *args]
    return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)


@pytest.mark.parametrize(
    ("lines", "code", "said"),
    [
        ({1: "30 40", 2: "10 40", 3: "20 20", 4: "0 0"}, 0, "убито 60 из 100 (60%)"),
        ({1: "30 40", 2: "9 40", 3: "20 20", 4: "0 0"}, 1, "ERROR"),
        ({1: "0 0", 2: "0 0", 3: "0 0", 4: "0 0"}, 0, "мутантов нет, судить нечего"),
        ({1: "30 40", 2: "10 40", 4: "0 0"}, 1, "нет счёта долей 3 "),
    ],
    ids=["total-60", "total-59", "all-empty", "shard-3-silent"],
)
def test_a289_sum_judges_the_total_and_names_a_silent_shard(
    tmp_path: Path, lines: dict[int, str], code: int, said: str
) -> None:
    """A289: порог — по сумме: доля с 25 % при общих 60 % — зелёное, общие 59 % — красное; все доли
    пусты — «судить нечего», не красное; доля без счёта — сумма не судит и называет её номер, а не
    считает по трём."""
    for shard, line in lines.items():
        (tmp_path / f"shard-{shard}-of-4.counts").write_text(f"{line}\n", encoding="utf-8")
    done = gate(LINT, MUTATION_SUM="4", MUTATION_COUNTS_DIR=str(tmp_path))
    out = done.stdout + done.stderr
    assert (done.returncode, said in out) == (code, True), out


@pytest.mark.parametrize("count", [1, 3, 4, 9])
def test_a289_each_file_goes_to_exactly_one_shard(count: int) -> None:
    """A289: доли покрывают изменённые файлы без пересечений — каждая порча файла ровно на одной
    машине; файлов меньше четырёх — лишние доли пусты, а не повтор чужих."""
    files = [f"backend/src/pkg/m{i}.py" for i in range(count)]
    script = 'green= yellow= reset=; not_judged() { exit 9; }; . "$1"; MUTATION_SHARD=$2; shift 2\n'
    script += 'changed=$(printf "%s\\n" "$@"); ts_shard && printf "%s\\n" "$changed"'
    shard = str(LINT / "mutation_shard.sh")
    picked = [
        [line for line in bash(script, shard, f"{k}/4", *files).stdout.splitlines() if "/m" in line]
        for k in range(1, 5)
    ]
    flat = [name for chosen in picked for name in chosen]
    empty = [chosen for chosen in picked if not chosen]
    assert (sorted(flat), len(set(flat)), len(empty)) == (files, len(flat), max(0, 4 - count))


def make_repo(root: Path, files: tuple[str, ...]) -> Path:
    """Копия гейта и файлы python в `backend/src`, изменённые после базы `HEAD~1`."""
    lint = root / "scripts" / "lint"
    lint.mkdir(parents=True)
    for part in PARTS:
        shutil.copy(LINT / part, lint / part)
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    git += ["-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], env=GIT_ENV, check=True)
    for text, message in (("X = 1\n", "база"), ("def f(x):\n    return x + 1\n", "правка")):
        for name in files:
            path = root / "backend" / "src" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        subprocess.run([*git, "add", "-A"], env=GIT_ENV, check=True)
        subprocess.run([*git, "commit", "-qm", message], env=GIT_ENV, check=True)
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path, ("pkg/a.py", "pkg/b.py"))


@pytest.mark.parametrize(
    ("shard", "base", "code", "said", "count"),
    [
        ("3/4", "HEAD~1", 0, "доля 3/4 — файлов на неё не пришлось", "0 0"),
        ("2/4", "HEAD", 0, "изменённых prod-файлов нет", "0 0"),
        ("1/4", "HEAD~1", 1, "mutmut не найден", None),
    ],
    ids=["no-files", "no-diff", "files"],
)
def test_a289_python_shard_counts_empty_and_judges_its_files(
    repo: Path, shard: str, base: str, code: int, said: str, count: str | None
) -> None:
    """A289: python-половина больше не отказывает доле; доле без файлов и запросу без python — счёт
    «0 0» и выход нулём без mutmut; доля с файлом идёт судить — до проверки инструментов, без
    счёта."""
    folder = repo / "counts"
    env = {"BASE": base, "LINT_PY_SRC": "backend/src", "MUTATION_SHARD": shard}
    done = gate(repo / "scripts" / "lint", MUTATION_COUNTS_DIR=str(folder), **env)
    out = done.stdout + done.stderr
    written = folder / f"shard-{shard[0]}-of-4.counts"
    found = written.read_text(encoding="utf-8").strip() if written.exists() else None
    assert (done.returncode, said in out, "только у TS-половины" in out) == (code, True, False), out
    assert found == count


#: Подделка mutmut: версия, а `run` пишет маски в файл `GLOBS_FILE` — порчи не гоняются.
FAKE_MUTMUT = """#!/usr/bin/env bash
case "$1" in
  --version) echo "mutmut, version 3.8.1" ;;
  run) shift; printf '%s\\n' "$@" >> "$GLOBS_FILE" ;;
esac
"""
#: Дифф: `__init__.py` пакета с модулем и модулем подпакета, и два модуля без изменённого пакета.
PACKAGE = ("pkg/__init__.py", "pkg/m.py", "pkg/sub/n.py", "other/a.py", "other/b.py")
#: Порчи по правилу имён mutmut 3.8.1: модуль, затем `x_функция__mutmut_N`; у `pkg/__init__.py`
#: модуль — `pkg`.
NAMES = (
    "pkg.x_f__mutmut_1",
    "pkg.m.x_g__mutmut_1",
    "pkg.sub.n.x_h__mutmut_1",
    "pkg.sub.x_s__mutmut_1",
    "pkg.u.x_u__mutmut_1",
    "other.a.x_a__mutmut_1",
    "other.b.x_b__mutmut_1",
    "other.c.x_c__mutmut_1",
)


def test_a289_package_init_and_its_modules_share_a_shard(tmp_path: Path) -> None:
    """A289, находка проверяющей 2026-11-23T12:16Z: маска `пакет/__init__.py` — `пакет.*` (канон)
    — выбирает и порчи модулей пакета; файлы под изменённым `__init__.py` идут в его долю, иначе
    одну порчу выбрали бы две доли и сумма сочла бы её дважды. Маски — из настоящего гейта, выбор —
    `fnmatch`, как у mutmut: каждую порчу выбирает не больше одной доли, а все доли вместе — то же,
    что одна машина."""
    root = make_repo(tmp_path / "repo", PACKAGE)
    fake = root / "backend" / ".venv" / "bin" / "mutmut"
    fake.parent.mkdir(parents=True)
    fake.write_text(FAKE_MUTMUT, encoding="utf-8")
    fake.chmod(0o755)
    picked: dict[str, set[str]] = {}
    for shard in ("", "1/4", "2/4", "3/4", "4/4"):
        globs = tmp_path / f"globs-{shard.replace('/', '-') or 'one'}.txt"
        globs.touch()
        env = {"BASE": "HEAD~1", "LINT_PY_SRC": "backend/src", "MUTATION_NO_BUDGET": "1"}
        env |= {"MUTATION_SHARD": shard, "GLOBS_FILE": str(globs)}
        gate(root / "scripts" / "lint", MUTATION_COUNTS_DIR=str(tmp_path / "counts"), **env)
        masks = globs.read_text(encoding="utf-8").split()
        picked[shard] = {name for name in NAMES if any(fnmatch.fnmatch(name, m) for m in masks)}
    shards = [picked[f"{k}/4"] for k in range(1, 5)]
    twice = sorted(name for name in NAMES if sum(name in chosen for chosen in shards) > 1)
    assert (twice, set().union(*shards)) == ([], picked[""])
    assert picked[""] == set(NAMES) - {"other.c.x_c__mutmut_1"}


def block(start: str, part: str = "mutation_verdict.sh") -> str:
    """Кусок части гейта от строки `start` до закрывающей скобки или `fi` блока."""
    text = (LINT / part).read_text(encoding="utf-8")
    begin = text.index(start)
    ends = [text.find(mark, begin) + len(mark) for mark in ("\nfi\n", "\n}\n")]
    return text[begin : min(end for end in ends if end > begin + 4)]


@pytest.mark.parametrize("shard", ["2/4", ""], ids=["shard", "whole-run"])
def test_a289_python_shard_counts_after_its_recheck_and_skips_the_threshold(
    tmp_path: Path, shard: str
) -> None:
    """A289: доля python порог не судит — самопроверка, счёт «убито всего» в файл доли и выход нулём
    даже при 10 % убитых; вне долей ветка не срабатывает и счёта не пишет."""
    head = f'. "{LINT / "mutation_shard.sh"}"; py_recheck() {{ echo "самопроверка"; }}\n'
    head += f'MUTATION_COUNTS_DIR="{tmp_path}"; MUTATION_SHARD="{shard}"; killed=1; total=10\n'
    code = head + block('if [[ -n "${MUTATION_SHARD:-}" ]]; then\n  py_recheck') + "echo порог\n"
    done = bash(code)
    counts = sorted(path.read_text(encoding="utf-8") for path in tmp_path.glob("*.counts"))
    if shard:
        said = "самопроверка\nmutation: доля 2/4 — убито 1 из 10; порог судит сумма долей"
        assert (done.returncode, said in done.stdout, counts) == (0, True, ["1 10\n"])
    else:
        assert (done.stdout, counts) == ("порог\n", [])


def test_a289_honest_nothing_in_a_shard_writes_zero_count(tmp_path: Path) -> None:
    """A289: честное «изменённые файлы не дали мутантов» у доли — счёт «0 0» и выход нулём: сумма
    без файла доли не отличила бы «нечего судить» от «доля не отчиталась»."""
    out = tmp_path / "mutmut.out"
    out.write_text("Filtered for specific mutants, but nothing matches\n", encoding="utf-8")
    head = f'. "{LINT / "mutation_shard.sh"}"; py_population() {{ return 0; }}; out="{out}"\n'
    head += f'MUTATION_COUNTS_DIR="{tmp_path}"; MUTATION_SHARD="4/4"; mut_globs=(pkg.a.*)\n'
    head += 'not_judged() { echo "не судил"; exit 7; }; yellow= reset=\n'
    done = bash(head + block("undecided() {") + "undecided\n")
    found = (tmp_path / "shard-4-of-4.counts").read_text(encoding="utf-8")
    assert (done.returncode, found) == (0, "0 0\n"), done.stdout + done.stderr


#: Подделка mutmut для самопроверки: две убитые порчи; `run` при `MARK` кладёт метку сбоя копии.
FAKE_RECHECK = """#!/usr/bin/env bash
case "$1" in
  results) printf '    pkg.x_f__mutmut_1: killed\\n    pkg.x_g__mutmut_1: killed\\n' ;;
  run) [[ -z "${MARK:-}" ]] || { mkdir -p "$MARK"; echo "копия базы не создана: проба" \\
    > "$MARK/pkg.x_f__mutmut_1.txt"; } ;;
esac
"""


@pytest.mark.parametrize("marked", [True, False], ids=["mark", "no-mark"])
def test_a285_a287_mark_written_during_recheck_stops_the_gate(tmp_path: Path, marked: bool) -> None:
    """A285, A287, находка проверяющей 2026-11-23T12:20Z: метки баз читаются и после самопроверки —
    сбой копии в её сессии даёт «гейт не просудил» с именем порчи, а не «живых нет» (код 3 упавшего
    начала сессии mutmut засчитывает убитым); без метки — прежний вердикт самопроверки."""
    fake = tmp_path / "mutmut"
    fake.write_text(FAKE_RECHECK, encoding="utf-8")
    fake.chmod(0o755)
    runs = tmp_path / "mutants" / "db-copies"
    head = f'. "{LINT / "mutation_shard.sh"}"; yellow= reset= red=; RECHECK=10; RECHECK_SEC=120\n'
    head += f'MUTMUT="{fake}"; REPO_ROOT="{tmp_path}"; MUT_CWD=.; out="{tmp_path / "out"}"\n'
    head += f'killed=2; MUTATION_WORKERS=4; TIMEOUT_BIN=; db_run="{runs}"\n'
    head += f'export MARK="{runs / "failed"}"\n' if marked else ""
    head += 'not_judged() { echo "не просудил"; exit 7; }\n'
    code = head + block("db_marks() {", "mutation_py.sh") + block("py_recheck() {") + "py_recheck\n"
    done = bash(code)
    out = done.stdout + done.stderr
    if marked:
        named = "pkg.x_f__mutmut_1 — копия базы не создана: проба"
        assert (done.returncode, named in out, "живых нет" in out) == (7, True, False), out
    else:
        assert (done.returncode, "2 убитых перепрогнаны последовательно, живых нет" in out) == (
            0,
            True,
        )
