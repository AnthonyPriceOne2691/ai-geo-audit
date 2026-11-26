"""Скрипт выката, файл окружения сервера (6а-2, срез 3; A182): `env-init` создаёт файл, мастер-ключ
и каталог входящих, `put-secret` вносит секреты владельца файлом. Имена и пометки — из блока
«сервер» `.env.example`; внешних команд эти команды не зовут. Без Docker и без сети."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from deploy_fakes import ROOT, make_ctx, tool

from ai_geo_audit.config import master_key
from ai_geo_audit.config import startup_checks as sc

SAMPLE = ROOT / ".env.example"
MARKS = ("создаёт скрипт", "задаёт владелец", "пишет выкат")
MARK_RE = re.compile(r"^## (создаёт скрипт|задаёт владелец|пишет выкат)$")
LINE_RE = re.compile(r"^# ([A-Z][A-Z0-9_]*)=(.*)$")
#: Блок «сервер» образца литералами теста — по пометкам, в порядке образца.
PASSWORDS = ["DB_PASSWORD", "POSTGRES_MIGRATE_PASSWORD", "POSTGRES_APP_PASSWORD", "REDIS_PASSWORD"]
ADDRESSES: dict[str, tuple[str, sc.Process | None]] = {
    "MIGRATE_DATABASE_URL": ("POSTGRES_MIGRATE_PASSWORD", "migrate"),
    "API_DATABASE_URL": ("POSTGRES_APP_PASSWORD", "api"),
    "API_REDIS_URL": ("REDIS_PASSWORD", None),
}
CREATED = [*PASSWORDS, *ADDRESSES, "JWT_SECRET", "KEYS_MASTER_KEY_HOST_FILE"]
OWNER = [
    "SITE_DOMAIN",
    "WEB_PORT",
    "FRONT_SUBNET",
    "WEB_ADDRESS",
    "NEIGHBOR_HEALTH_URLS",
    "ALERT_TELEGRAM_BOT_TOKEN",
    "ALERT_TELEGRAM_CHAT_ID",
]
DEPLOY = ["BACKEND_IMAGE", "WEB_IMAGE"]
#: Имена файла сервера, которые читает только скрипт, не compose.
SCRIPT_ONLY = {"SITE_DOMAIN", "NEIGHBOR_HEALTH_URLS"}
OWNER_FILE = "ALERT_TELEGRAM_BOT_TOKEN=1234567:owner-token-x\nALERT_TELEGRAM_CHAT_ID=-1004567\n"


def sample_block(text: str) -> dict[str, tuple[str, str]]:
    """Блок «сервер»: имя → пометка и значение образца; блок кончается строкой не-комментарием."""
    block: dict[str, tuple[str, str]] = {}
    mark = None
    for line in text.splitlines():
        if found := MARK_RE.match(line):
            mark = found[1]
        elif mark and (pair := LINE_RE.match(line)):
            block[pair[1]] = (mark, pair[2])
        elif mark and not line.startswith("#"):
            mark = None
    return block


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Корни — во временном каталоге, образец — копия из рабочей копии, как в клоне на сервере;
    `/etc` есть, свой корень в нём заводит env-init; владелец мастер-ключа — пользователь прогона
    (на сервере — uid 10001)."""
    ctx = make_ctx(tmp_path, monkeypatch)
    ctx.key_owner = os.getuid()
    ctx.paths.etc.parent.mkdir()
    ctx.paths.service.joinpath(".env.example").write_bytes(SAMPLE.read_bytes())
    return ctx


def env_init(ctx: Any) -> int:
    return tool.main(["env-init"], ctx)


def put_secret(ctx: Any, path: Path) -> int:
    return tool.main(["put-secret", str(path)], ctx)


def values(ctx: Any) -> dict[str, str]:
    text = ctx.paths.env_file.read_text(encoding="utf-8")
    return dict(line.split("=", 1) for line in text.splitlines())


def incoming(ctx: Any, text: str, mode: int = 0o600, name: str = "owner.env") -> Path:
    path = ctx.paths.etc / "incoming" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def said(ctx: Any) -> str:
    """Вывод и журнал хода всех запусков."""
    logs = "".join(p.read_text(encoding="utf-8") for p in ctx.paths.deploy_dir.glob("*.log"))
    return ctx.out.getvalue() + logs


def test_a182_server_block_names_and_marks_in_the_sample() -> None:
    """A182: блок «сервер» образца — 18 имён по трём пометкам: «создаёт скрипт», «задаёт
    владелец», «пишет выкат»; у паролей и адресов — заглушка (A176)."""
    block = sample_block(SAMPLE.read_text(encoding="utf-8"))
    by_mark = {mark: [n for n, (m, _) in block.items() if m == mark] for mark in MARKS}
    assert by_mark == dict(zip(MARKS, (CREATED, OWNER, DEPLOY), strict=True))
    assert [block[name][1] for name in DEPLOY] == ["", ""]


def test_a182_server_block_covers_every_compose_variable() -> None:
    """A182: файл сервера — окружение compose: каждое `${ИМЯ…}` основного файла и боевого
    наложения есть в блоке «сервер»; сверх них — только имена, что читает сам скрипт."""
    used: set[str] = set()
    for name in ("docker-compose.yml", "docker-compose.prod.yml"):
        lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
        code = [line.split("#", 1)[0] for line in lines]
        used |= set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", "\n".join(code)))
    block = set(sample_block(SAMPLE.read_text(encoding="utf-8")))
    assert (used - block, block - used) == (set(), SCRIPT_ONLY)


def test_a182_env_init_creates_the_server_file_by_the_marks(ctx: Any) -> None:
    """A182: файла окружения нет — env-init создаёт его: права 600, владелец — пользователь
    запуска (root на сервере); в нём ровно имена блока «сервер» в его порядке: «создаёт скрипт» —
    случайные значения, пароли — 48 знаков hex, секрет подписи — 64; «задаёт владелец» —
    заглушка; «пишет выкат» — пусто; путь мастер-ключа — свой корень `/etc`."""
    assert env_init(ctx) == 0
    info = ctx.paths.env_file.stat()
    assert (stat.S_IMODE(info.st_mode), info.st_uid) == (0o600, ctx.owner)
    got = values(ctx)
    assert list(got) == [*CREATED, *OWNER, *DEPLOY]
    assert all(re.fullmatch(r"[0-9a-f]{48}", got[name]) for name in PASSWORDS), got.keys()
    assert re.fullmatch(r"[0-9a-f]{64}", got["JWT_SECRET"])
    assert len({got[name] for name in (*PASSWORDS, "JWT_SECRET")}) == 5
    assert [got[name] for name in OWNER] == [tool.PLACEHOLDER] * len(OWNER)
    assert [got[name] for name in DEPLOY] == ["", ""]
    assert got["KEYS_MASTER_KEY_HOST_FILE"] == str(ctx.paths.etc / "master.key")
    assert ctx.runner.calls == []


def test_a182_addresses_carry_the_role_and_queue_passwords(ctx: Any) -> None:
    """A182: адреса migrate, api и очереди несут те же пароли, что пароли ролей и очереди; роль в
    адресе базы — из таблицы ролей проверки старта (A174); таблица пар скрипта — та же."""
    env_init(ctx)
    got = values(ctx)
    for url_name, (password_name, process) in ADDRESSES.items():
        parts = urlsplit(got[url_name])
        assert parts.password == got[password_name], url_name
        assert process is None or parts.username == sc.DB_ROLES[process], url_name
    assert {url: password for url, (password, _) in ADDRESSES.items()} == tool.PAIRS


def test_a182_master_key_and_incoming_folder(ctx: Any) -> None:
    """A182: мастер-ключ — `master.key` своего корня `/etc`: одна строка из 64 знаков hex — формат
    A220, его принимает разбор пакета; права 400, владелец — uid процесса api (на сервере 10001);
    каталог — 700, каталог входящих — 700."""
    env_init(ctx)
    key = ctx.paths.etc / "master.key"
    info = key.stat()
    assert (stat.S_IMODE(info.st_mode), info.st_uid) == (0o400, ctx.key_owner)
    assert re.fullmatch(r"[0-9a-f]{64}\n", key.read_text(encoding="ascii"))
    assert len(master_key.read(str(key))) == 1
    assert stat.S_IMODE(ctx.paths.etc.stat().st_mode) == 0o700
    assert stat.S_IMODE(ctx.paths.etc.joinpath("incoming").stat().st_mode) == 0o700
    assert tool.Context().key_owner == 10001


def test_a182_env_init_says_how_many_and_the_owner_names_without_values(ctx: Any) -> None:
    """A182: в выводе и журнале — «создано: 9», имена, что задаёт владелец, и что пишет выкат;
    ни одного значения, ни мастер-ключа."""
    env_init(ctx)
    seen = said(ctx)
    assert "создано: 9" in seen
    assert f"задаёт владелец — put-secret: {', '.join(OWNER)}" in seen
    assert f"пишет выкат: {', '.join(DEPLOY)}" in seen
    secret = [v for v in values(ctx).values() if len(v) >= 12 and v != tool.PLACEHOLDER]
    key = ctx.paths.etc.joinpath("master.key").read_text(encoding="ascii").strip()
    assert [v for v in [*secret, key] if v in seen] == []


def test_a182_second_env_init_is_refused_and_recreates_nothing(ctx: Any) -> None:
    """A182: файл окружения уже есть — отказ словами; файл и мастер-ключ не тронуты: база помнит
    пароли первого запуска, новый секрет подписи гасит все пропуска."""
    env_init(ctx)
    kept = [ctx.paths.env_file, ctx.paths.etc / "master.key"]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in kept]
    assert env_init(ctx) == 1
    assert [(p.read_bytes(), p.stat().st_mtime_ns) for p in kept] == before
    assert "отказ — файл окружения уже есть — значения не пересоздаются" in said(ctx)


def test_a182_master_key_without_env_file_is_refused(ctx: Any) -> None:
    """A182, соседний случай: мастер-ключ есть, файла окружения нет (прерванный запуск) — отказ:
    новый ключ сделал бы ключи сервисов в базе нечитаемыми; ключ не тронут, файла нет."""
    ctx.paths.etc.mkdir(parents=True)
    ctx.paths.etc.joinpath("master.key").write_text("old\n", encoding="ascii")
    assert env_init(ctx) == 1
    assert ctx.paths.etc.joinpath("master.key").read_text(encoding="ascii") == "old\n"
    assert not ctx.paths.env_file.exists()
    assert "мастер-ключ уже есть, файла окружения нет" in said(ctx)


def test_a182_dangling_link_in_place_of_the_server_file_is_refused_at_once(
    ctx: Any, tmp_path: Path
) -> None:
    """A182, A186, мелочь 4 третьей проверяющей: на месте файла сервера — висячая ссылка во
    временный каталог: отказ сразу, ещё на проверке «файл уже есть» — функция файлов по ссылкам не
    ходит; мастер-ключа нет, по ссылке ничего не создано. Порча «`exists` вместо `lexists`»
    поэтому равнозначна: `own` отказывает раньше (проба 2026-11-26, decisions)."""
    target = tmp_path / "elsewhere" / "env"
    ctx.paths.env_file.symlink_to(target)
    assert env_init(ctx) == 1
    assert f"отказ — {ctx.paths.env_file} — ссылка: скрипт по ссылкам не ходит" in said(ctx)
    assert not ctx.paths.etc.joinpath("master.key").exists()
    assert not target.exists()


def test_a182_server_block_ends_at_the_first_line_outside_comments(ctx: Any) -> None:
    """A182, мелочь 1 третьей проверяющей: блок «сервер» кончается первой строкой не-комментарием —
    `LATER_SETTING=1` и `# LATER_EXAMPLE=x` после него в файл сервера не идут: имён 18, не 19."""
    sample = ctx.paths.service / ".env.example"
    text = sample.read_text(encoding="utf-8")
    sample.write_text(text + "\nLATER_SETTING=1\n# LATER_EXAMPLE=x\n", encoding="utf-8")
    assert env_init(ctx) == 0
    assert list(values(ctx)) == [*CREATED, *OWNER, *DEPLOY]


@pytest.mark.parametrize(
    ("change", "why"),
    [
        (lambda t: re.sub(r"(?m)^## .*\n", "", t), "в образце нет блока «сервер»"),
        (
            lambda t: t.replace(
                "## задаёт владелец\n", "# NEW_SECRET=CHANGE_ME\n## задаёт владелец\n"
            ),
            "NEW_SECRET: «создаёт скрипт», а скрипт не знает, как его сделать",
        ),
    ],
    ids=["нет-блока", "чужое-имя-скрипта"],
)
def test_a182_env_init_refuses_a_sample_it_does_not_know(ctx: Any, change: Any, why: str) -> None:
    """A182: блок «сервер» образца и таблица скрипта сходятся: нет блока или у «создаёт скрипт»
    имя, которого скрипт не умеет, — отказ до записи, файла окружения нет."""
    sample = ctx.paths.service / ".env.example"
    sample.write_text(change(sample.read_text(encoding="utf-8")), encoding="utf-8")
    assert env_init(ctx) == 1
    assert not ctx.paths.env_file.exists()
    assert why in said(ctx)


def test_a182_put_secret_replaces_own_lines_and_removes_the_incoming_file(ctx: Any) -> None:
    """A182: `put-secret` — значения из входящего файла заменой своих строк, порядок и прочие
    строки прежние, права 600; входящий удалён; в выводе — «внесено: 2» и имена, без значений."""
    env_init(ctx)
    before = values(ctx)
    path = incoming(ctx, OWNER_FILE)
    assert put_secret(ctx, path) == 0
    after = values(ctx)
    pair = {
        "ALERT_TELEGRAM_BOT_TOKEN": "1234567:owner-token-x",
        "ALERT_TELEGRAM_CHAT_ID": "-1004567",
    }
    assert after == before | pair
    assert list(after) == list(before)
    assert stat.S_IMODE(ctx.paths.env_file.stat().st_mode) == 0o600
    assert not path.exists()
    seen = said(ctx)
    assert "внесено: 2 — ALERT_TELEGRAM_BOT_TOKEN, ALERT_TELEGRAM_CHAT_ID" in seen
    assert "owner-token-x" not in seen and "-1004567" not in seen
    assert ctx.runner.calls == []


@pytest.mark.parametrize(
    ("text", "mode", "why"),
    [
        ("JWT_SECRET=from-incoming\n", 0o600, "JWT_SECRET — не «задаёт владелец»"),
        ("BACKEND_IMAGE=x\n", 0o600, "BACKEND_IMAGE — не «задаёт владелец»"),
        ("UNKNOWN_NAME=x\n", 0o600, "UNKNOWN_NAME — не «задаёт владелец»"),
        (OWNER_FILE, 0o640, "права входящего файла шире 600"),
        ("not a line\n", 0o600, "входящий файл, строка 1: не «ИМЯ=значение»"),
        ("", 0o600, "входящий файл пуст — нечего вносить"),
    ],
    ids=["создаёт-скрипт", "пишет-выкат", "чужое", "права", "не-строка", "пустой"],
)
def test_a182_put_secret_refuses_and_touches_nothing(
    ctx: Any, text: str, mode: int, why: str
) -> None:
    """A182: чужое имя, имя другой пометки, широкие права, строка не «ИМЯ=значение» — отказ с
    именем, без значения; ни файл сервера, ни входящий не тронуты."""
    env_init(ctx)
    before = ctx.paths.env_file.read_bytes()
    path = incoming(ctx, text, mode)
    assert put_secret(ctx, path) == 1
    assert ctx.paths.env_file.read_bytes() == before
    assert path.read_text(encoding="utf-8") == text
    assert why in said(ctx)


def test_a182_put_secret_refuses_a_name_missing_in_the_server_file(ctx: Any) -> None:
    """A182, находка 2 третьей проверяющей: в файле сервера нет имени «задаёт владелец» (образец
    вырос после env-init) — отказ с именем; ни файл сервера, ни входящий не тронуты. Порча «без
    отказа» — «итог: ок», входящий удалён, имени в файле нет: секрет владельца пропал молча."""
    env_init(ctx)
    rows = ctx.paths.env_file.read_text(encoding="utf-8").splitlines(keepends=True)
    trimmed = "".join(row for row in rows if not row.startswith("SITE_DOMAIN="))
    ctx.paths.env_file.write_text(trimmed, encoding="utf-8")
    before = ctx.paths.env_file.read_bytes()
    path = incoming(ctx, "SITE_DOMAIN=example.test\n")
    assert put_secret(ctx, path) == 1
    assert ctx.paths.env_file.read_bytes() == before
    assert path.read_text(encoding="utf-8") == "SITE_DOMAIN=example.test\n"
    assert "в файле окружения нет имён: SITE_DOMAIN" in said(ctx)


def test_a182_second_put_secret_finds_no_incoming_file(ctx: Any) -> None:
    """A182: тот же входящий второй раз — его уже нет: отказ словами, файл сервера прежний."""
    env_init(ctx)
    path = incoming(ctx, OWNER_FILE)
    assert put_secret(ctx, path) == 0
    after = ctx.paths.env_file.read_bytes()
    assert put_secret(ctx, path) == 1
    assert ctx.paths.env_file.read_bytes() == after
    assert "входящего файла нет — положить его в каталог входящих" in said(ctx)


def test_a182_put_secret_takes_only_the_incoming_folder(ctx: Any, tmp_path: Path) -> None:
    """A182: входящий — только из каталога входящих: свой корень вне него и чужой путь — отказ;
    файлы на месте."""
    env_init(ctx)
    beside = ctx.paths.etc / "owner.env"
    beside.write_text(OWNER_FILE, encoding="utf-8")
    beside.chmod(0o600)
    outside = tmp_path / "owner.env"
    outside.write_text(OWNER_FILE, encoding="utf-8")
    outside.chmod(0o600)
    assert (put_secret(ctx, beside), put_secret(ctx, outside)) == (1, 1)
    assert beside.exists() and outside.exists()
    assert ctx.out.getvalue().count("входящий файл — только из каталога входящих") == 2


def test_a182_put_secret_before_env_init_is_refused(ctx: Any) -> None:
    """A182: файла окружения ещё нет — отказ «сначала env-init»; входящий на месте."""
    path = incoming(ctx, OWNER_FILE)
    assert put_secret(ctx, path) == 1
    assert path.exists()
    assert "файла окружения нет — сначала env-init" in said(ctx)


def test_a186_env_init_and_put_secret_write_only_under_own_roots(ctx: Any) -> None:
    """A186, растёт на env-init и put-secret: все записи, переименования, права и удаления — одной
    функцией файлов и только под своими корнями; внешних команд нет."""
    env_init(ctx)
    put_secret(ctx, incoming(ctx, OWNER_FILE))
    roots = tuple(ctx.paths.roots)
    paths = [Path(path) for op in ctx.files.ops for path in op[1:]]
    kinds = {op[0] for op in ctx.files.ops}
    assert {"write", "chown", "remove", "mkdir"} <= kinds
    assert [p for p in paths if not any(p.is_relative_to(root) for root in roots)] == []
    assert ctx.runner.calls == []
