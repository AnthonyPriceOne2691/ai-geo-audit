"""Живой `deploy.py check` шагом оракула (6а-3, T3; A185, A183): помощник root-части
`scripts/deploy_check_live.py` и шаг `scripts/stack_oracle_check.py` — без root и без Docker. Ответы
`deploy.py check` для шага — настоящие: ядро на подставной машине, где стек прогона оракула —
«сосед» с нашей приставкой, а вымышленный сосед объявляет порт строкой compose. Прогон — в CI."""

from __future__ import annotations

import importlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import dbtools
import deploy_records as rec
import pytest
from deploy_fakes import ROOT, make_ctx, server_values, tool, write_env

sys.path.insert(0, str(dbtools.ROOT / "scripts"))
compose_mod = importlib.import_module("stack_oracle_compose")

HELPER = ROOT / "scripts" / "deploy_check_live.py"
NAME = "ai-geo-audit-oracle-0123456789"
#: Порт web и подсеть `front` стека прогона — выдуманы в ряду оракула (A82). Чистые — тот же ряд
#: от имени шага; первую его подсеть держит сеть машины — её шаг обходит.
PORT, SUBNET = 18123, "172.16.0.32/28"
NETS = [SUBNET, compose_mod.web_slot(f"{NAME}-check", lambda _port, _net: False)[1]]
CLEAN_PORT, CLEAN_SUBNET = compose_mod.web_slot(f"{NAME}-check", lambda _port, net: net in NETS)
URL = f"http://127.0.0.1:{PORT}/"
SVERIT = "у соседа порт задан переменной — сверить"
LINE = (
    "судимых строк 8; столкновение со стеком прогона — код 1: имена ок, порт занят и объявлен у "
    "соседа, сверить, подсеть — отказ; чистые значения — имена, порт и подсеть ок, сверить, "
    "код 0 по строкам; значений в выводе 0"
)


def load_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("deploy_check_live", HELPER)
    assert spec is not None and spec.loader is not None, HELPER
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tree(root: Path) -> list[str]:
    """Всё под корнем, и висячие ссылки: «ничего не тронуто» сверяется по нему."""
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


@pytest.fixture
def helper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    """Помощник на машине CI от root; корни и сосед — во временном каталоге, клон — образец."""
    module = load_helper()
    (tmp_path / "clone" / "deploy").mkdir(parents=True)
    (tmp_path / "clone" / "deploy" / "deploy.py").write_text("# выкат\n", encoding="utf-8")
    places = {"ROOT": "clone", "SERVICE": "srv/ai-geo-audit", "ETC": "etc/ai-geo-audit"}
    for name, rel in (places | {"NEIGHBOR": "srv/nb-oracle"}).items():
        monkeypatch.setattr(module, name, tmp_path / rel)
    (tmp_path / "srv").mkdir()
    (tmp_path / "etc").mkdir()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setenv("NB_DECLARED_PORT", str(PORT))
    return module


@pytest.mark.parametrize(
    ("case", "why"),
    [
        ({"GITHUB_ACTIONS": None}, "только на машине джобы CI"),
        ({"GITHUB_ACTIONS": "false", "argv": ["owner"]}, "только на машине джобы CI"),
        ({"uid": 1001, "argv": ["clean"]}, "нужен root"),
        ({"argv": ["drop"]}, "режим — roots, owner или clean"),
        ({"argv": ["roots", "clean"]}, "режим — roots, owner или clean"),
        ({"NB_DECLARED_PORT": "порт"}, "нет в окружении: NB_DECLARED_PORT"),
    ],
    ids=["нет-CI", "CI-false", "не-root", "режим", "два-режима", "порт-соседа"],
)
def test_a185_live_helper_refuses_before_any_write(
    helper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    case: dict[str, Any],
    why: str,
) -> None:
    """A185: помощник пишет в `/srv` и `/etc` от root — не на машине CI, не от root, режим не
    `roots`, `owner` или `clean`, порт соседа не число — отказ с кодом 2 до любой записи."""
    for name in {"GITHUB_ACTIONS", "NB_DECLARED_PORT"} & case.keys():
        if case[name] is None:
            monkeypatch.delenv(name)
        else:
            monkeypatch.setenv(name, case[name])
    monkeypatch.setattr(os, "geteuid", lambda: case.get("uid", 0))
    before = tree(tmp_path)
    assert helper.main(case.get("argv", ["roots"])) == 2
    assert why in capsys.readouterr().err
    assert tree(tmp_path) == before


@pytest.mark.parametrize("kind", ["каталог", "ссылка"])
@pytest.mark.parametrize("root", ["SERVICE", "ETC", "NEIGHBOR"])
def test_a185_live_helper_refuses_when_a_root_or_the_neighbor_exists(
    helper: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path, root: str, kind: str
) -> None:
    """A185: корень сервиса, свой `/etc` или каталог соседа уже есть — каталогом или висячей
    ссылкой — отказ «машина не чистая», ничего не тронуто (ведущая и третья, 2026-11-27)."""
    path: Path = getattr(helper, root)
    if kind == "каталог":
        path.mkdir()
    else:
        path.symlink_to(tmp_path / "нет")
    before = tree(tmp_path)
    assert helper.main(["roots"]) == 2
    assert "машина не чистая" in capsys.readouterr().err
    assert tree(tmp_path) == before


def test_a185_live_helper_roots_then_clean_take_exactly_their_paths(
    helper: ModuleType, tmp_path: Path
) -> None:
    """A185, план «Исполнение рисковых путей»: корень сервиса — копия клона, вымышленный сосед
    `/srv/nb-oracle` (decisions) — порт строкой с умолчанием из окружения и порт переменной без
    умолчания; свой `/etc` заводит `env-init`; уборка снимает ровно корень, `/etc` и соседа."""
    for other in ("srv/other", "etc/other"):
        (tmp_path / other).mkdir()
    assert helper.main(["roots"]) == 0
    assert (helper.SERVICE / "deploy" / "deploy.py").is_file() and not helper.ETC.exists()
    text = (helper.NEIGHBOR / "docker-compose.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:${NB_WEB_PORT:-18123}:80"' in text
    assert '"127.0.0.1:${NB_API_PORT}:8000"' in text
    (helper.ETC / "incoming").mkdir(parents=True)
    assert helper.main(["clean"]) == 0
    assert tree(tmp_path / "srv") == ["other"] and tree(tmp_path / "etc") == ["other"]
    assert helper.main(["roots"]) == helper.main(["clean"]) == 0  # `env-init` не дошёл: `/etc` нет
    assert tree(tmp_path / "srv") == ["other"]


def test_a185_live_helper_clean_without_its_mark_touches_nothing(
    helper: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A185: корни есть, но не помощника (метки у соседа нет) — отказ, ничего не тронуто."""
    for path in (helper.SERVICE, helper.ETC, helper.NEIGHBOR):
        path.mkdir()
    before = tree(tmp_path)
    assert helper.main(["clean"]) == 2
    assert "не заведены шагом" in capsys.readouterr().err
    assert tree(tmp_path) == before


def test_a185_live_helper_clean_failure_keeps_the_mark_for_a_retry(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A185: корень не снялся — отказ, сосед с меткой цел (он снимается последним), повтор `clean`
    убирает остальное."""
    assert helper.main(["roots"]) == 0
    (helper.ETC / "incoming").mkdir(parents=True)
    real = shutil.rmtree

    def busy(path: Path) -> None:
        if path == helper.ETC:
            raise OSError("занято")
        real(path)

    monkeypatch.setattr(shutil, "rmtree", busy)
    assert helper.main(["clean"]) == 2 and (helper.NEIGHBOR / helper.MARK).is_file()
    monkeypatch.setattr(shutil, "rmtree", real)
    assert helper.main(["clean"]) == 0 and tree(tmp_path / "srv") == tree(tmp_path / "etc") == []


@pytest.mark.parametrize("where", ["copytree", "write_text"], ids=["посреди-копии", "файл-соседа"])
def test_a185_live_helper_roots_failure_takes_back_what_it_made(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, where: str
) -> None:
    """A185: сбой посреди заведения корней, и посреди самой копии, — заведённое снято, полкопии
    не остаётся, код 2; чужое не тронуто."""
    (tmp_path / "srv" / "other").mkdir()

    def broken(*args: Any, **_kwargs: Any) -> None:
        if where == "copytree":
            (Path(args[1]) / "deploy").mkdir(parents=True)
        raise OSError("диск")

    monkeypatch.setattr(shutil if where == "copytree" else Path, where, broken)
    assert helper.main(["roots"]) == 2
    assert tree(tmp_path / "srv") == ["other"]


def test_a183_live_helper_owner_file_from_the_environment(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A183: файл владельца — в каталоге входящих, права 600, ровно имена «задаёт владелец»,
    значения — из окружения (`--preserve-env`), не из аргументов; нет значения или оно пустое —
    отказ с именем, файла нет; файл уже есть — отказ, не перезапись."""
    (helper.ETC / "incoming").mkdir(parents=True)
    values = {name: f"value-of-{name.lower()}" for name in helper.OWNER}
    for name in helper.OWNER[:-1]:
        monkeypatch.setenv(name, values[name])
    monkeypatch.delenv(helper.OWNER[-1], raising=False)
    assert helper.main(["owner"]) == 2
    assert f"нет в окружении: {helper.OWNER[-1]}" in capsys.readouterr().err
    path = helper.ETC / "incoming" / "owner.env"
    assert not path.exists()
    monkeypatch.setenv(helper.OWNER[-1], "")
    assert helper.main(["owner"]) == 2 and not path.exists()
    monkeypatch.setenv(helper.OWNER[-1], values[helper.OWNER[-1]])
    assert helper.main(["owner"]) == 0
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.read_text(encoding="utf-8") == "".join(f"{k}={v}\n" for k, v in values.items())
    assert helper.main(["owner"]) == 2
    assert "owner: FileExistsError" in capsys.readouterr().err


def test_a185_live_helper_owner_names_are_the_owner_names_of_the_sample() -> None:
    """A185: имена файла владельца — ровно «задаёт владелец» блока «сервер» образца: новое имя
    владельца без значения — красное здесь, а не в CI."""
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    block = text.split("## задаёт владелец\n", 1)[1].split("## ", 1)[0]
    names = [line[2:].split("=", 1)[0] for line in block.splitlines() if line.startswith("# ")]
    assert list(load_helper().OWNER) == names


def real_check(tmp_path: Path, port: int, subnet: str, **change: Any) -> tuple[int, str]:
    """Настоящий вывод `deploy.py check` на машине джобы: стек прогона оракула — «сосед» с нашей
    приставкой (web публикует свой порт, своя сеть `front`), вымышленный сосед — строки портов.
    Подстановки ядра — на время ответа: подстановки шага не трогаются."""
    rows = load_helper().COMPOSE.replace("{port}", str(PORT)).splitlines()
    ports = [f"/srv/nb-oracle/docker-compose.yml: {row}\n" for row in rows if "- " in row]
    with pytest.MonkeyPatch.context() as patch:
        ctx = make_ctx(tmp_path, patch)
        stack = [rec.inspect_line(f"{NAME}-{s}-1", NAME) for s in ("api", "db", "redis")]
        stack.append(rec.inspect_line(f"{NAME}-web-1", NAME, ports=rec.loopback(str(PORT))))
        if change.get("squatter"):
            stack.append(rec.inspect_line("ai-geo-audit-api-1", NAME))
        ctx.runner.become([*rec.server_machine(), *stack])
        if change.get("oracle_net", True):
            ctx.runner.networks.append(rec.network_line(f"{NAME}_front", NAME, SUBNET))
        lines = rec.compose_lines() + "".join(ports if change.get("neighbor", True) else [])
        memory = rec.meminfo(change.get("memory_gb", 50) * rec.GIB_KB)
        ctx.runner.answers |= {"find": (0, lines), "cat": (0, memory)}
        values = {"WEB_PORT": str(port), "FRONT_SUBNET": subnet, "NEIGHBOR_HEALTH_URLS": URL}
        write_env(ctx, server_values(**values, WEB_ADDRESS=compose_mod.web_address(subnet)))
        ctx.out = io.StringIO()
        code = tool.main(["check"], ctx)
    return code, ctx.out.getvalue()


def outputs(tmp_path: Path, **change: Any) -> list[tuple[int, str]]:
    """Два ответа `check`: значения, что сталкиваются со стеком прогона, и чистые."""
    collision = real_check(tmp_path / "a", PORT, SUBNET, **change)
    return [collision, real_check(tmp_path / "b", CLEAN_PORT, CLEAN_SUBNET, **change)]


class Sudo:
    """Подставной `sudo -n`: пишет каждую команду и её окружение; помощник и команды скрипта
    отвечают по режиму, `check` — ответами по очереди."""

    def __init__(self, checks: list[tuple[int, str]], **answers: tuple[int, str]) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
        self.checks = list(checks)
        self.answers = {"roots": (0, ""), "owner": (0, ""), "clean": (0, "")}
        self.answers |= {"env-init": (0, "создано: 9\n"), "put-secret": (0, "внесено: 7\n")}
        self.answers |= answers

    def __call__(self, *argv: str, env: dict[str, str] | None = None) -> Any:
        self.calls.append((argv, dict(env or {})))
        code, out = self.checks.pop(0) if argv[1:2] == ("check",) else self.answers[argv[1]]
        return subprocess.CompletedProcess(list(argv), code, out, "")

    def modes(self) -> list[str]:
        return [argv[1] for argv, _env in self.calls]


@pytest.fixture
def step(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Модуль шага на машине CI; проба bind подставная: занято только то, что в сетях машины."""
    module = importlib.import_module("stack_oracle_check")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(module, "busy_on", lambda nets: lambda _port, net: net in nets)
    return module


def run_of() -> SimpleNamespace:
    """Прогон оракула: имя, порт и подсеть стека; сети машины — ответы `network ls` и `inspect`."""

    def docker(*args: str, limit: str) -> str:
        return "n1\nn2\n" if args[1] == "ls" else "".join(f"{net} \n" for net in NETS)

    return SimpleNamespace(name=NAME, facts={"slot": (PORT, SUBNET)}, docker=docker)


def live(step: ModuleType, monkeypatch: pytest.MonkeyPatch, sudo: Sudo) -> str:
    monkeypatch.setattr(step, "sudo", sudo)
    result: str = step.step_live_check(run_of())
    return result


def test_a185_live_check_skips_outside_ci_in_words_and_writes_nothing(
    step: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A185: без `GITHUB_ACTIONS` — пропуск словами: шаг пишет в `/srv` и `/etc` от root; ни
    одной команды."""
    monkeypatch.delenv("GITHUB_ACTIONS")
    sudo = Sudo([])
    assert live(step, monkeypatch, sudo).startswith("пропуск: не машина CI")
    assert sudo.calls == []


def test_a185_live_check_in_ci_without_sudo_is_red_not_a_skip(
    step: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A185: на машине CI пропуска нет — `sudo -n` отказал (нужен пароль) — красное с кодом, а
    убирать нечего: корни не заведены."""
    sudo = Sudo([], roots=(1, "sudo: a password is required\n"))
    with pytest.raises(compose_mod.StepError, match=r"^корни сервера: код 1$"):
        live(step, monkeypatch, sudo)
    assert sudo.modes() == ["roots"]


def test_a185_a183_live_check_judges_the_collision_then_clean_values(
    step: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A185, план «Исполнение рисковых путей»: значения, что сталкиваются с живым стеком прогона,
    — имена «ок» (наша приставка под другим проектом), `WEB_PORT` занят и объявлен у соседа,
    «сверить», `FRONT_SUBNET` — отказ, код 1; затем чистые — мимо сетей машины, строки «ок»;
    корни сняты. A183: значения — окружением команд, в аргументах — режимы и пути."""
    sudo = Sudo(outputs(tmp_path))
    assert live(step, monkeypatch, sudo) == LINE
    rounds = ["owner", "put-secret", "check"]
    assert sudo.modes() == ["roots", "env-init", *rounds, *rounds, "clean"]
    values = [str(PORT), SUBNET, str(CLEAN_PORT), CLEAN_SUBNET, URL, "example.test"]
    assert not [arg for argv, _env in sudo.calls for arg in argv if any(v in arg for v in values)]
    owners = [env for argv, env in sudo.calls if argv[1] == "owner"]
    got = [(env["WEB_PORT"], env["FRONT_SUBNET"], env["NEIGHBOR_HEALTH_URLS"]) for env in owners]
    assert got == [(str(PORT), SUBNET, URL), (str(CLEAN_PORT), CLEAN_SUBNET, URL)]
    roots = [env for argv, env in sudo.calls if argv[1] == "roots"]
    assert roots == [{"NB_DECLARED_PORT": str(PORT)}]


@pytest.mark.parametrize(
    "failure",
    [None, subprocess.TimeoutExpired("sudo", 1), OSError()],
    ids=["запуск", "предел-времени", "нет-sudo"],
)
def test_a183_sudo_keeps_values_in_the_environment(
    monkeypatch: pytest.MonkeyPatch, failure: Exception | None
) -> None:
    """A183: `sudo -n --preserve-env=<имена>` — значения в окружении процесса, не `sudo env X=…`
    и не аргументами: argv видны в списке процессов. Сбой запуска — красное с видом ошибки."""
    module = importlib.import_module("stack_oracle_check")
    seen: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command: list[str], **kwargs: Any) -> Any:
        seen.append((command, kwargs["env"]))
        if failure is not None:
            raise failure
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    values = {"WEB_PORT": "18123", "SITE_DOMAIN": "example.test"}
    if failure is not None:
        with pytest.raises(compose_mod.StepError, match=f"^sudo owner: {type(failure).__name__}$"):
            module.sudo("/x/helper.py", "owner", env=values)
        return
    module.sudo("/x/helper.py", "owner", env=values)
    keep = "--preserve-env=GITHUB_ACTIONS,WEB_PORT,SITE_DOMAIN"
    assert seen[0][0] == ["sudo", "-n", keep, "python3", "-I", "/x/helper.py", "owner"]
    assert (seen[0][1]["WEB_PORT"], seen[0][1]["SITE_DOMAIN"]) == ("18123", "example.test")


@pytest.mark.parametrize(
    ("change", "at", "spoil", "why"),
    [
        ({"squatter": True}, 0, None, "имена — не «ок»"),
        ({"neighbor": False}, 0, None, "порт — нет «объявлен у соседа»; нет «сверить»"),
        ({"oracle_net": False}, 0, None, "подсеть — нет отказа FRONT_SUBNET"),
        ({}, 0, (f"порт: {SVERIT}", "порт: ок", 1), "нет «сверить»"),
        ({}, 0, ("WEB_PORT занят; ", "", 1), "порт — нет «занят»"),
        ({}, 0, ("итог:", f"{SUBNET} итог:", 1), "значения в выводе check: FRONT_SUBNET"),
        ({}, 0, ("", "", 0), "код 0, а отказов в строках 2"),
        ({}, 1, ("подсеть: ок", "подсеть: x", 0), "подсеть — не «ок»"),
        ({}, 1, ("порт: ок", "порт; ок", 0), "порт — не «ок»"),
        ({}, 1, ("", "", 1), "код 1, а отказов в строках 0"),
        ({}, 1, ("общих", "x — красное: общих", 0), "код 0, а отказов в строках 1"),
    ],
    ids=[
        *("имена-не-ок", "сосед-не-объявил", "подсеть-не-отказ", "без-сверить", "порт-не-занят"),
        *("значение", "код-0-при-отказах", "чистая-подсеть", "чистый-порт", "код-1-без-отказа"),
        "красный-снимок",
    ],
)
def test_a185_live_check_red_on_a_wrong_line_and_roots_taken_back(
    step: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    change: dict[str, bool],
    at: int,
    spoil: tuple[str, str, int] | None,
    why: str,
) -> None:
    """A185, A183: строка не та, значение в выводе, код не по строкам (отказ — «отказ — …», у
    снимка соседа — «— красное:») — красное с причиной словами, без значений; корни сняты."""
    found = outputs(tmp_path, **change)
    if spoil is not None:
        found[at] = (spoil[2], found[at][1].replace(spoil[0], spoil[1]))
    sudo = Sudo(found)
    with pytest.raises(compose_mod.StepError) as red:
        live(step, monkeypatch, sudo)
    assert str(red.value) == f"{('столкновение', 'чистые значения')[at]}: {why}"
    assert sudo.modes()[-1] == "clean"


def test_a185_live_check_resources_line_is_not_judged_but_the_code_follows_it(
    step: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A185: строку ресурсов шаг не судит — у раннера другие память и диск, — но код выхода
    идёт за ней: чистые значения при отказе ресурсов — код 1, шаг зелёный и называет код."""
    sudo = Sudo(outputs(tmp_path, memory_gb=9))
    assert live(step, monkeypatch, sudo) == LINE.replace("код 0 по строкам", "код 1 по строкам")


@pytest.mark.parametrize(
    ("answers", "why"),
    [
        ({"env-init": (1, "")}, "env-init: код 1"),
        ({"put-secret": (1, "")}, "put-secret: код 1"),
        ({"owner": (2, "")}, "файл владельца: код 2"),
        ({"clean": (2, "")}, "уборка корней: код 2"),
        ({"env-init": (1, ""), "clean": (2, "")}, "env-init: код 1; уборка корней: код 2"),
    ],
    ids=["env-init", "put-secret", "файл-владельца", "уборка", "красное-и-уборка"],
)
def test_a185_live_check_red_on_a_failed_move_and_roots_taken_back(
    step: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    answers: dict[str, tuple[int, str]],
    why: str,
) -> None:
    """A185: ход не удался — красное с кодом; корни снимаются и на красном (`finally`). Уборка не
    удалась — красное: иначе `deploy_live.py` откажет «машина не чистая»; причина до неё цела."""
    sudo = Sudo(outputs(tmp_path), **answers)
    with pytest.raises(compose_mod.StepError, match=f"^{why}$"):
        live(step, monkeypatch, sudo)
    assert sudo.modes()[-1] == "clean"
