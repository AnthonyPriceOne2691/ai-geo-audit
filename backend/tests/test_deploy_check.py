"""Скрипт выката, предполётная проверка `deploy.py check` (Ч1.6а, 6а-3): A185; A181, третья
строка; A189, вторая строка; A186 растёт на `deploy.py check`. Машина — подставной исполнитель с
записями разведки и раннера (`deploy_records`), свободное место — подставное; без Docker и без
сети. Строки вывода — как их читает ведущая: «тема: ок», «тема: отказ — причины» или названное
исключение; код не 0 — отказ хоть в одной строке."""

from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import deploy_records as rec
import pytest
import yaml
from deploy_fakes import GIB, ROOT, make_ctx, server_values, tool, write_env

#: Строка вывода: время UTC с Z, тема — до первого двоеточия, текст.
LINE_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ ([^:]+): (.*)")
DIGEST = "sha256:" + "ab" * 32
GOOD_IMAGES = {
    "BACKEND_IMAGE": f"ai-geo-audit-backend@{DIGEST}",
    "WEB_IMAGE": f"registry.example.test:5000/ai-geo-audit/web@{DIGEST}",
}
NOT_DIGEST = "не <путь>@sha256:<64 знака>"
PAIRS = "пары «пароль — адрес»"
FIRST_DEPLOY = ["образов ещё нет — первый выкат"]
#: Наш стек поднят: пределы наложения у запущенных сервисов; миграции — разовые, остановлены.
UP = {"api": 512 << 20, "db": 1 << 30, "redis": 256 << 20, "web": 128 << 20}


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Сервер перед первым выкатом (разведка): файл сервера после `env-init` и `put-secret`,
    своего стека нет; исполнитель, проба и свободное место — подставные (L38)."""
    made = make_ctx(tmp_path, monkeypatch)
    made.runner.become(rec.server_machine())
    write_env(made, server_values())
    return made


def check(ctx: Any) -> tuple[int, dict[str, list[str]]]:
    """Код `deploy.py check` и строки его вывода по темам."""
    ctx.out = io.StringIO()
    code = tool.main(["check"], ctx)
    lines: dict[str, list[str]] = {}
    for row in ctx.out.getvalue().splitlines():
        if found := LINE_RE.fullmatch(row):
            lines.setdefault(found[1], []).append(found[2])
    return code, lines


def refused(*reasons: str) -> list[str]:
    return ["отказ — " + "; ".join(reasons)]


def said(ctx: Any) -> str:
    """Вывод и журналы хода `.deploy/`."""
    logs = sorted(ctx.paths.deploy_dir.glob("*.log"))
    return ctx.out.getvalue() + "".join(log.read_text(encoding="utf-8") for log in logs)


def neighbor_file(line: str) -> tuple[int, str]:
    """Ответ разбора файлов compose соседа: записи разведки и ещё одна строка третьего проекта."""
    return 0, rec.compose_lines() + f"/srv/nb-gamma/docker-compose.yml: {line}\n"


def ss_with(local: str) -> tuple[int, str]:
    """Ответ `ss`: записи разведки и ещё один слушающий сокет."""
    return 0, rec.listening() + f"LISTEN 0      4096   {local}   0.0.0.0:*\n"


def address(secret_part: str, host: str = "db:5432/a") -> str:
    """Адрес базы роли приложения — из частей: в исходнике нет адреса с паролем (A122)."""
    return "postgresql+asyncpg://app:" + secret_part + "@" + host


def test_records_compose_config_follow_our_compose_files() -> None:
    """Запись ответа `docker compose config` — по нашим файлам: сервисы, сети и том основного
    файла, пределы памяти наложения; иначе подставной ответ молча отстал бы от файлов."""
    main, prod = (
        yaml.safe_load((ROOT / name).read_text(encoding="utf-8").replace("!reset ", ""))
        for name in ("docker-compose.yml", "docker-compose.prod.yml")
    )
    config = json.loads(rec.compose_config())
    assert set(config["services"]) == set(main["services"]) == set(prod["services"])
    limits = {name: service["mem_limit"] for name, service in prod["services"].items()}
    assert {name: service["mem_limit"] for name, service in config["services"].items()} == limits
    assert set(config["networks"]) == set(main["networks"])
    assert set(config["volumes"]) == set(main["volumes"])


def test_a185_check_before_the_first_deploy_every_line_ok_or_a_named_exception(ctx) -> None:
    """A185 (шестая строка — на сервере, здесь — машина разведки): перед первым выкатом каждая
    строка — «ок» или названное исключение «образов ещё нет — первый выкат», код 0; итог
    называет число строк. Порт соседа из файла и его живой порт — оба не наш."""
    code, lines = check(ctx)
    assert code == 0, lines
    assert lines["образы"] == FIRST_DEPLOY
    for topic in ("файл окружения", PAIRS, "имена", "порт", "подсеть"):
        assert lines[topic] == ["ок"], topic
    assert lines["ресурсы"] == [
        "ок — памяти после наших пределов 47 ГБ, места: каталог докера 200 ГБ, корень копий 200 ГБ"
    ]
    assert lines["снимок соседа"] == ["общих 35, перезапусков 0, коды прежние; пропало 0, новых 0"]
    assert (lines["проверено строк"], lines["итог"]) == (["8, отказов: 0"], ["ок"])


@pytest.mark.parametrize(
    ("change", "mode", "owner", "reason"),
    [
        ({}, 0o640, 0, "права файла окружения шире 600 — chmod 600"),
        ({}, 0o600, 1, "владелец файла окружения не root — chown root"),
        ({"JWT_SECRET": tool.PLACEHOLDER}, 0o600, 0, "JWT_SECRET: заглушка CHANGE_ME"),
        (
            {"ALERT_TELEGRAM_CHAT_ID": tool.PLACEHOLDER},
            0o600,
            0,
            "ALERT_TELEGRAM_CHAT_ID: заглушка CHANGE_ME",
        ),
        ({"redis": ""}, 0o600, 0, "REDIS_PASSWORD: пусто"),
    ],
    ids=["права", "владелец", "заглушка-скрипта", "заглушка-владельца", "пусто"],
)
def test_a185_check_refuses_the_server_file_by_name_and_reason(
    ctx, change: dict[str, str], mode: int, owner: int, reason: str
) -> None:
    """A185, первая строка: права шире 600, владелец не root, заглушка у имени «создаёт скрипт»
    или «задаёт владелец», пустое значение — отказ `deploy.py check` с именем и причиной, без
    значения; пустые образы до первого выката — не отказ (A181)."""
    values = server_values(**change)
    write_env(ctx, values, mode=mode)
    ctx.owner += owner
    code, lines = check(ctx)
    assert (code, lines["файл окружения"], lines["образы"]) == (1, refused(reason), FIRST_DEPLOY)
    # Заглушка — слово ядра в тексте отказа, а не значение.
    shown = [value for value in values.values() if len(value) > 7 and value in said(ctx)]
    assert [value for value in shown if value != tool.PLACEHOLDER] == []


@pytest.mark.parametrize(
    ("images", "expected"),
    [
        ({}, FIRST_DEPLOY),
        (GOOD_IMAGES, ["ок"]),
        (
            GOOD_IMAGES | {"BACKEND_IMAGE": "ai-geo-audit-backend:latest"},
            refused(f"BACKEND_IMAGE: {NOT_DIGEST}"),
        ),
        (
            GOOD_IMAGES | {"WEB_IMAGE": "ai-geo-audit-web@sha256:" + "ab" * 31},
            refused(f"WEB_IMAGE: {NOT_DIGEST}"),
        ),
        (
            GOOD_IMAGES | {"WEB_IMAGE": "ai-geo-audit-web@sha256:" + "AB" * 32},
            refused(f"WEB_IMAGE: {NOT_DIGEST}"),
        ),
        ({"BACKEND_IMAGE": GOOD_IMAGES["BACKEND_IMAGE"]}, refused(f"WEB_IMAGE: {NOT_DIGEST}")),
    ],
    ids=["до-первого-выката", "дайджесты", "тег", "короткий-дайджест", "заглавные", "один-пуст"],
)
def test_a181_check_images_in_the_server_file(
    ctx, images: dict[str, str], expected: list[str]
) -> None:
    """A181, третья строка: до первого выката оба образа пусты — строка «образов ещё нет — первый
    выкат», не отказ; потом — только `<путь>@sha256:<64 знака>`, иначе отказ `deploy.py check` с
    именем переменной, без значения."""
    write_env(ctx, server_values(**images))
    code, lines = check(ctx)
    assert (code, lines["образы"]) == (int(expected[0].startswith("отказ")), expected)
    assert "latest" not in said(ctx) and "ab" * 31 not in said(ctx)


@pytest.mark.parametrize(("url", "password"), list(tool.PAIRS.items()), ids=list(tool.PAIRS))
def test_a189_check_refuses_an_address_whose_password_differs(ctx, url: str, password: str) -> None:
    """A189, вторая строка: пароль в адресе не равен паролю роли или очереди — отказ «пароль в
    <адрес> не равен <пароль>»; значений в выводе нет."""
    values = server_values()
    write_env(ctx, values | {url: values[url].replace(values[password], "e5" * 24)})
    code, lines = check(ctx)
    assert (code, lines[PAIRS]) == (1, refused(f"пароль в {url} не равен {password}"))
    assert "e5e5" not in said(ctx) and values[password] not in said(ctx)


def test_a189_check_compares_the_password_after_percent_decoding(ctx) -> None:
    """A189, вторая строка: сравнение — после раскодирования процентной записи: тот же пароль,
    записанный в адресе `%XX`, — «ок»; адрес, который разборщик не читает, — отказ словами."""
    spelled = "ab@cd/ef:gh%ij"
    values = server_values(app=spelled) | {"API_DATABASE_URL": address(quote(spelled, safe=""))}
    write_env(ctx, values)
    code, lines = check(ctx)
    assert (code, lines[PAIRS]) == (0, ["ок"])
    write_env(ctx, values | {"API_DATABASE_URL": address("x", "[db/a")})
    code, lines = check(ctx)
    assert (code, lines[PAIRS]) == (1, refused("API_DATABASE_URL: адрес не разобран"))
    assert "gh%ij" not in said(ctx)


#: A185, вторая строка: чужой объект с нашим именем — элемент на каждый вид (L37).
SQUATTERS = [
    ("container", rec.inspect_line("ai-geo-audit-api-1", "nb-alpha")),
    ("container", rec.inspect_line("ai-geo-audit-migrate-1", "", status="exited", health=None)),
    ("network", rec.network_line("ai-geo-audit_front", "nb-beta", "10.9.0.0/24")),
    ("network", rec.network_line("ai-geo-audit_data", "", "10.9.1.0/24")),
    ("volume", rec.volume_line("ai-geo-audit_db-data", "nb-alpha")),
    ("volume", rec.volume_line("ai-geo-audit_db-data")),
]


@pytest.mark.parametrize(
    ("kind", "line"),
    SQUATTERS,
    ids=["контейнер", "контейнер-без-метки", "сеть", "сеть-без-метки", "том", "том-без-метки"],
)
def test_a185_check_refuses_our_name_held_by_another_stack(ctx, kind: str, line: str) -> None:
    """A185, вторая строка: контейнер, сеть или том с нашим именем под другой меткой проекта
    compose или без неё — отказ «имя занято не нашим стеком: N», без имён."""
    if kind == "container":
        ctx.runner.become([*rec.server_machine(), line])
    elif kind == "network":
        ctx.runner.networks.append(line)
    else:
        ctx.runner.volumes.append(line)
    code, lines = check(ctx)
    assert (code, lines["имена"]) == (1, refused("имя занято не нашим стеком: 1"))
    assert "nb-alpha" not in said(ctx) and "nb-beta" not in said(ctx)


def test_a185_check_own_stack_and_stacks_with_our_prefix_are_no_hindrance(ctx) -> None:
    """A185, вторая строка: свои объекты стека — не помеха; стек с нашей приставкой под другим
    проектом (оракул, разработка) — имена не наши, ложного отказа нет."""
    oracle = "ai-geo-audit-oracle-0123456789"
    others = [rec.inspect_line(f"{oracle}-api-1", oracle)]
    others.append(rec.inspect_line("ai-geo-audit-dev-db-1", "ai-geo-audit-dev"))
    ctx.runner.become([*rec.server_machine(), *rec.ours(), *others])
    ctx.runner.networks.append(rec.network_line("ai-geo-audit_edge", "ai-geo-audit", "10.9.3.0/24"))
    ctx.runner.networks.append(rec.network_line(f"{oracle}_front", oracle, "10.9.2.0/28"))
    ctx.runner.volumes += [
        rec.volume_line("ai-geo-audit_db-data", "ai-geo-audit"),
        rec.volume_line(f"{oracle}_db-data", oracle),
        rec.volume_line("ai-geo-audit-dev_db", "ai-geo-audit-dev"),
    ]
    code, lines = check(ctx)
    assert (code, lines["имена"]) == (0, ["ок"])


@pytest.mark.parametrize(
    "local", ["127.0.0.1:18100", "0.0.0.0:18100", "*:18100", "[::]:18100", "[::1]:18100"]
)
def test_a185_check_port_listened_by_another_process_is_taken(ctx, local: str) -> None:
    """A185, третья строка: `WEB_PORT` слушает другой процесс — на петле или на всех адресах, в
    видах `ss`, которых на сервере нет (`*:P`, `[::1]:P`), тоже, — «WEB_PORT занят», без
    порта."""
    ctx.runner.answers["ss"] = ss_with(local)
    code, lines = check(ctx)
    assert (code, lines["порт"]) == (1, refused("WEB_PORT занят"))
    assert "18100" not in said(ctx)


@pytest.mark.parametrize("status", ["running", "exited"])
def test_a185_check_port_published_by_a_foreign_container_is_taken(ctx, status: str) -> None:
    """A185, третья строка: `WEB_PORT` публикует чужой контейнер — и остановленный: привязка
    остаётся за ним, — «WEB_PORT занят»."""
    web = rec.inspect_line("nb-gamma-web-1", "nb-gamma", status=status, ports=rec.loopback("18100"))
    ctx.runner.become([*rec.server_machine(), web])
    code, lines = check(ctx)
    assert (code, lines["порт"]) == (1, refused("WEB_PORT занят"))


def test_a185_check_port_of_our_own_web_is_no_hindrance(ctx) -> None:
    """A185: `WEB_PORT` публикует наш web — повторный выкат; его сокет в `ss` — не помеха."""
    web = rec.inspect_line("ai-geo-audit-web-1", "ai-geo-audit", ports=rec.loopback("18100"))
    ctx.runner.become([*rec.server_machine(), web])
    ctx.runner.answers["ss"] = ss_with("127.0.0.1:18100")
    code, lines = check(ctx)
    assert (code, lines["порт"]) == (0, ["ок"])


@pytest.mark.parametrize(
    ("port", "reasons"),
    [
        ("9180", ["WEB_PORT объявлен у соседа"]),
        ("9102", ["WEB_PORT занят"]),
        ("9101", ["WEB_PORT занят", "WEB_PORT объявлен у соседа"]),
        ("5544", ["WEB_PORT объявлен у соседа"]),
        ("8000", ["WEB_PORT объявлен у соседа"]),
    ],
    ids=["умолчание-файла", "живой-порт", "живой-и-в-файле", "разработка-строкой", "разработка"],
)
def test_a185_check_port_against_the_three_server_facts(ctx, port: str, reasons: list[str]) -> None:
    """A185, третья строка, — три факта сервера (разведка, блок 11; сверка ведущей 2026-11-27), у
    каждого свой случай: умолчание в файле соседа (`9180`) не равно его живому порту (`9102`) —
    судятся оба; файлы разработки рядом с боевыми объявляют порты на всех интерфейсах, хотя не
    подняты, — «объявлен у соседа»; адрес привязки переменной (`${WEB_BIND:-…}`) порт не прячет."""
    write_env(ctx, server_values(WEB_PORT=port))
    code, lines = check(ctx)
    assert (code, lines["порт"]) == (1, refused(*reasons))


#: A185, третья строка: виды строк портов в файле compose соседа — элемент на каждый (L37).
DECLARED = {
    "строка-списка": '      - "127.0.0.1:${NB_PORT:-18100}:80"',
    "без-кавычек": "      - 18100:8080",
    "список-строкой": '    ports: ["5544:5432", "18100:80"]',
    "длинная-запись": "          published: 18100",
    "длинная-первым-ключом": '        - published: "18100"',
    "диапазон": '      - "18095-18105:8000-8010"',
    "протокол": '      - "18100:53/udp"',
    "ipv6": '      - "[::1]:18100:80"',
    "адрес-переменной-без-умолчания": '      - "${NB_BIND}:18100:80"',
    "комментарий-в-строке": '      - "18100:80"  # для отладки',
}


@pytest.mark.parametrize("line", DECLARED.values(), ids=DECLARED.keys())
def test_a185_check_port_declared_in_a_neighbor_compose_file(ctx, line: str) -> None:
    """A185, третья строка: `WEB_PORT` в строке портов файла compose соседа — и у сервиса, что
    не поднят, — «WEB_PORT объявлен у соседа». Видов записи, кроме строки списка и списка
    строкой, у соседа нет (разведка, блок 11) — их держит этот тест."""
    ctx.runner.answers["find"] = neighbor_file(line)
    code, lines = check(ctx)
    assert (code, lines["порт"]) == (1, refused("WEB_PORT объявлен у соседа"))


@pytest.mark.parametrize(
    "spec",
    [
        '- "127.0.0.1:${NB_PORT}:80"',
        '- "${NB_PORT:?порт соседа}:80"',
        "- $NB_PORT:80",
        "  published: ${NB_PORT}",
        "- published: abc",
    ],
    ids=["без-умолчания", "обязательная", "без-скобок", "длинная-запись", "не-число"],
)
def test_a185_check_neighbor_port_by_a_variable_without_default_is_to_check(ctx, spec: str) -> None:
    """A185, третья строка: порт соседа задан переменной без умолчания (или не читается) —
    строка «у соседа порт задан переменной — сверить», не отказ: файлов окружения соседа скрипт
    не читает."""
    ctx.runner.answers["find"] = neighbor_file(f"      {spec}")
    code, lines = check(ctx)
    assert (code, lines["порт"]) == (0, ["ок", "у соседа порт задан переменной — сверить"])
    assert not any(".env" in arg for argv, _env in ctx.runner.calls for arg in argv)


def test_a185_check_skips_our_own_compose_files(ctx) -> None:
    """A185: строки своих файлов compose — наш клон лежит там же, на глубине 2, — не сосед."""
    ours = f'{ctx.paths.service}/docker-compose.dev.yml:       - "18100:8080"\n'
    ctx.runner.answers["find"] = (0, rec.compose_lines() + ours)
    code, lines = check(ctx)
    assert (code, lines["порт"]) == (0, ["ок"])


@pytest.mark.parametrize(
    "port", ["0", "65536", "80a", "", tool.PLACEHOLDER, "-1", "+80", "\u0668\u0660"]
)
def test_a185_check_refuses_a_port_that_is_not_a_port(ctx, port: str) -> None:
    """A185: `WEB_PORT` не число от 1 до 65535 — и цифры не ASCII — отказ с именем, без
    значения."""
    write_env(ctx, server_values(WEB_PORT=port))
    code, lines = check(ctx)
    assert (code, lines["порт"]) == (1, refused("WEB_PORT: не число от 1 до 65535"))


def test_a185_check_subnet_overlapping_a_neighbor_docker_network(ctx) -> None:
    """A185, четвёртая строка: `FRONT_SUBNET` пересекается с подсетью чужой сети докера — сосед
    завёл ещё сеть /16 из пула по умолчанию (вид — сверка ведущей 2026-11-27), — отказ с именем
    переменной."""
    ctx.runner.networks.append(rec.network_line("nb-alpha_jobs", "nb-alpha", "172.30.0.0/16"))
    write_env(ctx, server_values(FRONT_SUBNET="172.30.7.0/28", WEB_ADDRESS="172.30.7.2"))
    code, lines = check(ctx)
    assert (code, lines["подсеть"]) == (1, refused("FRONT_SUBNET пересекается с сетью докера"))
    assert "172.30" not in said(ctx)


def test_a185_check_subnet_inside_the_default_bridge_meets_network_and_route(ctx) -> None:
    """A185, четвёртая строка: подсеть внутри моста по умолчанию (разведка, блоки 6 и 9) —
    пересечение и с сетью докера, и с маршрутом хоста: обе причины."""
    write_env(ctx, server_values(FRONT_SUBNET="172.17.200.0/28", WEB_ADDRESS="172.17.200.2"))
    code, lines = check(ctx)
    reasons = (
        "FRONT_SUBNET пересекается с сетью докера",
        "FRONT_SUBNET пересекается с маршрутом хоста",
    )
    assert (code, lines["подсеть"]) == (1, refused(*reasons))


@pytest.mark.parametrize(
    "line",
    [
        "        - subnet: 198.51.100.0/26",
        '          subnet: "198.51.100.0/24"',
        "          ip_range: 198.51.100.0/29",
        "          gateway: 198.51.100.1",
        "        - subnet: ${NB_NET:-198.51.100.0/25}",
    ],
    ids=["подсеть", "в-кавычках", "диапазон", "шлюз", "переменная-с-умолчанием"],
)
def test_a185_check_subnet_overlapping_a_neighbor_compose_subnet(ctx, line: str) -> None:
    """A185, четвёртая строка: подсеть из файла compose соседа — строки `subnet`, `ip_range`,
    `gateway` разведки, блок 12, — отказ с именем переменной."""
    ctx.runner.answers["find"] = neighbor_file(line)
    code, lines = check(ctx)
    reason = "FRONT_SUBNET пересекается с подсетью из файла compose соседа"
    assert (code, lines["подсеть"]) == (1, refused(reason))


def test_a185_check_neighbor_subnet_by_a_variable_without_default_is_to_check(ctx) -> None:
    """A185, четвёртая строка: подсеть соседа задана переменной без умолчания — строка
    «сверить», не отказ."""
    ctx.runner.answers["find"] = neighbor_file("        - subnet: ${NB_NET}")
    code, lines = check(ctx)
    assert (code, lines["подсеть"]) == (0, ["ок", "у соседа подсеть задана переменной — сверить"])


@pytest.mark.parametrize(
    "route",
    [
        "198.51.100.0/24 dev enp2s0 proto kernel scope link src 198.51.100.5 ",
        "198.51.100.3 dev wg0 scope link ",
        "unreachable 198.51.100.0/27 proto static ",
    ],
    ids=["подсеть", "узел", "с-типом"],
)
def test_a185_check_subnet_overlapping_a_host_route(ctx, route: str) -> None:
    """A185, четвёртая строка: `FRONT_SUBNET` пересекается с маршрутом хоста — подсетью, узлом
    или маршрутом с типом, — отказ с именем переменной."""
    ctx.runner.answers["ip"] = (0, rec.routes() + route + "\n")
    code, lines = check(ctx)
    assert (code, lines["подсеть"]) == (1, refused("FRONT_SUBNET пересекается с маршрутом хоста"))


def test_a185_check_own_network_and_its_route_are_no_hindrance(ctx) -> None:
    """A185: своя сеть `front` и её маршрут — повторный выкат — не помеха."""
    ctx.runner.networks.append(
        rec.network_line("ai-geo-audit_front", "ai-geo-audit", "198.51.100.0/28")
    )
    own = "198.51.100.0/28 dev br-0a1b2c3d4e5f proto kernel scope link src 198.51.100.1 \n"
    ctx.runner.answers["ip"] = (0, rec.routes() + own)
    code, lines = check(ctx)
    assert (code, lines["подсеть"]) == (0, ["ок"])


@pytest.mark.parametrize(
    ("front", "web", "reason"),
    [
        ("198.51.100.0/28", "198.51.100.20", "WEB_ADDRESS вне FRONT_SUBNET"),
        ("198.51.100.0/28", "2001:db8::2", "WEB_ADDRESS вне FRONT_SUBNET"),
        ("198.51.100.0/28", "198.51.100.x", "WEB_ADDRESS: не адрес IPv4"),
        ("198.51.100.1/28", "198.51.100.2", "FRONT_SUBNET: не подсеть IPv4 вида a.b.c.d/N"),
        ("2001:db8::/64", "198.51.100.2", "FRONT_SUBNET: не подсеть IPv4 вида a.b.c.d/N"),
        (tool.PLACEHOLDER, "198.51.100.2", "FRONT_SUBNET: не подсеть IPv4 вида a.b.c.d/N"),
    ],
    ids=["вне", "ipv6", "не-адрес", "биты-узла", "подсеть-ipv6", "заглушка"],
)
def test_a185_check_web_address_and_subnet_spelling(ctx, front: str, web: str, reason: str) -> None:
    """A185, четвёртая строка: `WEB_ADDRESS` вне `FRONT_SUBNET` и негодная запись подсети или
    адреса — отказ с именем переменной, без значения."""
    write_env(ctx, server_values(FRONT_SUBNET=front, WEB_ADDRESS=web))
    code, lines = check(ctx)
    assert (code, lines["подсеть"]) == (1, refused(reason))
    assert "198.51.100.20" not in said(ctx)


def test_a185_check_memory_after_our_limits_below_ten_is_refused_with_numbers(ctx) -> None:
    """A185, пятая строка: свободной памяти (`MemAvailable`) после наших пределов меньше 10 ГБ —
    8 соседу и 2 запаса — отказ с числами в ГБ; на первом выкате считаются все наши пределы
    (2,125 ГБ): 12 − 2,125 — 9."""
    ctx.runner.answers["cat"] = (0, rec.meminfo(12 * rec.GIB_KB))
    code, lines = check(ctx)
    reason = "памяти после наших пределов 9 ГБ, нужно не меньше 10: 8 соседу и 2 запаса"
    assert (code, lines["ресурсы"]) == (1, refused(reason))


def test_a185_check_counts_the_difference_with_our_raised_stack(ctx) -> None:
    """A185, пятая строка: дальше первого выката — разница с поднятым стеком: пределы запущенных
    своих сервисов уже взяты; разовые миграции остановлены и не считаются. 11 ГБ: без стека —
    отказ (8), со стеком — нужно лишь 0,25 ГБ миграций — «ок» (10)."""
    ctx.runner.answers["cat"] = (0, rec.meminfo(11 * rec.GIB_KB))
    reason = "памяти после наших пределов 8 ГБ, нужно не меньше 10: 8 соседу и 2 запаса"
    assert check(ctx)[1]["ресурсы"] == refused(reason)
    up = [
        rec.inspect_line(f"ai-geo-audit-{name}-1", "ai-geo-audit", memory=size)
        for name, size in UP.items()
    ]
    up.append(
        rec.inspect_line(
            "ai-geo-audit-migrate-1", "ai-geo-audit", status="exited", memory=256 << 20
        )
    )
    ctx.runner.become([*rec.server_machine(), *up])
    code, lines = check(ctx)
    assert (code, lines["ресурсы"][0].split(",")[0]) == (
        0,
        "ок — памяти после наших пределов 10 ГБ",
    )


@pytest.mark.parametrize(
    ("where", "name"), [("docker", "в каталоге докера"), ("backups", "в корне копий")]
)
def test_a185_check_disk_below_twenty_is_refused_with_numbers(ctx, where: str, name: str) -> None:
    """A185, пятая строка: свободного места в каталоге докера (`docker info`) или в корне копий
    меньше 20 ГБ — отказ с числами в ГБ."""
    path = Path("/var/lib/docker") if where == "docker" else ctx.paths.backups
    ctx.space.free[path] = 19 * GIB + GIB // 2
    code, lines = check(ctx)
    assert (code, lines["ресурсы"]) == (1, refused(f"места {name} 19 ГБ, нужно не меньше 20"))
    assert set(ctx.space.asked) == {Path("/var/lib/docker"), ctx.paths.backups}


def test_a185_check_refuses_an_unread_memory_limit_of_ours(ctx) -> None:
    """A185, пятая строка: предел памяти своего сервиса не читается — посчитать нечего: отказ с
    именем сервиса."""
    ctx.runner.answers["docker compose"] = (0, rec.compose_config(rec.LIMITS | {"api": None}))
    code, lines = check(ctx)
    assert (code, lines["ресурсы"]) == (
        1,
        refused("предел памяти не разобран: api — сверить наложение"),
    )


@pytest.mark.parametrize(
    ("raw", "size"),
    [
        ("1g", 1 << 30),
        ("512m", 512 << 20),
        ("512M", 512 << 20),
        ("128mb", 128 << 20),
        ("1024k", 1 << 20),
        ("100", 100),
        ("100b", 100),
        (536870912, 536870912),
        ("1.5g", None),
        ("", None),
        (None, None),
        (True, None),
        ("${MEM}", None),
    ],
)
def test_a185_memory_limit_read_as_docker_reads_it(raw: object, size: int | None) -> None:
    """A185, пятая строка: предел памяти compose — байты или число с единицей `b`, `k`, `m`, `g`
    (и `kb`, `mb`, `gb`), двоичные; остальное — не разобран."""
    assert tool.limit_bytes(raw) == size


def test_free_space_of_a_missing_root_is_taken_at_the_nearest_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A185, пятая строка: место корня копий до первой копии (каталога ещё нет) — у ближайшего
    каталога выше; считается место для записи (`f_bavail`), а не всё свободное."""
    asked: list[Path] = []

    def statvfs(path: object) -> os.statvfs_result:
        asked.append(Path(str(path)))
        return os.statvfs_result((4096, 4096, 100, 50, 7, 0, 0, 0, 0, 255))

    monkeypatch.setattr(tool.os, "statvfs", statvfs)
    assert tool.free_space(tmp_path / "no" / "such") == 7 * 4096
    assert asked == [tmp_path]


def test_a185_check_exit_code_is_not_zero_exactly_when_a_line_is_refused(ctx) -> None:
    """Код выхода `deploy.py check` — не 0 тогда и только тогда, когда отказ хоть в одной строке
    (на нём стоит живой шаг оракула); названные исключения код не трогают; итог называет темы
    отказов, без значений."""
    ctx.runner.answers["ss"] = ss_with("127.0.0.1:18100")
    write_env(ctx, server_values(WEB_ADDRESS="198.51.100.20"))
    code, lines = check(ctx)
    assert (code, lines["проверено строк"]) == (1, ["8, отказов: 2"])
    assert lines["итог"] == refused(
        "отказ в строках: порт, подсеть — поправить и снова deploy.py check"
    )


@pytest.mark.parametrize(
    ("key", "answer", "reason"),
    [
        ("docker compose", (1, ""), "docker compose config: код 1"),
        ("docker compose", (0, "{"), "ответ docker compose config не разобран"),
        ("docker compose", (0, '{"services": 1}\n'), "ответ docker compose config не разобран"),
        ("docker network ls", (1, ""), "docker network ls: код 1"),
        ("docker network ls", (0, "сеть\n"), "ответ docker network ls не разобран"),
        ("docker network inspect", (1, ""), "docker network inspect: код 1"),
        ("docker network inspect", (0, "[]\n"), "ответ docker network inspect не разобран"),
        (
            "docker network inspect",
            (0, '{"name":"x","labels":{},"ipam":[{"Subnet":"300.0.0.0/8"}]}\n'),
            "ответ docker network inspect не разобран",
        ),
        ("docker volume ls", (0, "том\n"), "ответ docker volume ls не разобран"),
        ("ss", (1, ""), "ss: код 1"),
        ("ss", (0, "LISTEN 0 4096\n"), "ответ ss не разобран"),
        ("find", (1, ""), "файлы compose соседа: код 1"),
        ("find", (0, "строка без пути\n"), "ответ разбора файлов compose соседа не разобран"),
        ("ip", (1, ""), "ip route: код 1"),
        ("ip", (0, "abc dev x\n"), "ответ ip route не разобран"),
        ("cat", (0, "MemTotal: 1 kB\n"), "память: MemAvailable не прочитан"),
        ("docker info", (0, "\n"), "docker info: каталог докера не прочитан"),
    ],
)
def test_a185_check_unread_machine_answer_is_a_refusal_in_words(
    ctx, key: str, answer: tuple[int, str], reason: str
) -> None:
    """A185: ответ машины не читается — отказ словами, что запустить руками, а не сбой и не
    тихое «ок»."""
    ctx.runner.answers[key] = answer
    code, lines = check(ctx)
    assert code == 1
    assert lines["итог"][0].startswith(f"отказ — {reason}"), lines["итог"]


def test_a186_check_reads_the_machine_and_writes_only_its_journal_and_lock(ctx) -> None:
    """A186, растёт на `deploy.py check`: команды — только чтение машины (подставной исполнитель
    краснеет на запрещённой), у compose — стек `ai-geo-audit` и наши файлы из клона; на диск —
    только свои журнал хода и замок; файлов окружения соседа — нет."""
    assert check(ctx)[0] == 0
    assert {op[0] for op in ctx.files.ops} == {"mkdir", "append", "lock"}
    assert all(Path(op[1]).is_relative_to(ctx.paths.deploy_dir) for op in ctx.files.ops)
    compose = [argv for argv, _env in ctx.runner.calls if argv[:2] == ("docker", "compose")]
    assert [arg for arg in compose[0] if arg.endswith(".yml")] == [
        str(ctx.paths.service / "docker-compose.yml"),
        str(ctx.paths.service / "docker-compose.prod.yml"),
    ]
    assert len(compose) == 1 and compose[0][2:4] == ("--project-name", "ai-geo-audit")
    assert all(env == {} for _argv, env in ctx.runner.calls)
    assert not any(".env" in arg for argv, _env in ctx.runner.calls for arg in argv)
