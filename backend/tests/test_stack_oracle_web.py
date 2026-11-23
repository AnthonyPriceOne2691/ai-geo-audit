"""Чистые части шагов web оракула стека без Docker (A77–A84): контекст сборки и состав образа
web, слушающие порты контейнера, код страницы, ответ web с заголовками, CSP, отказы и журнал web;
шаги страницы, заголовков и CSP на подставном web, сбои шагов `/api/` на подставном стеке. Модуль
`scripts/stack_oracle_web.py` лежит вне пакета.
"""

from __future__ import annotations

import importlib
import json
import socket
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from email.message import Message
from types import SimpleNamespace

import dbtools
import pytest

sys.path.insert(0, str(dbtools.ROOT / "scripts"))
web = importlib.import_module("stack_oracle_web")

CONTEXT = {"frontend/package-lock.json", "frontend/src/main.tsx", "deploy/web/nginx.conf"}
DIST = {"index.html", "assets/index-AbCd1234.js"}
IMAGE = {f"usr/share/nginx/html/{name}" for name in DIST} | {"etc/nginx/conf.d/web.conf"}


@pytest.mark.parametrize(
    ("names", "problem"),
    [
        (CONTEXT, ""),
        (CONTEXT - {"deploy/web/nginx.conf"}, "нет открытого пути deploy/web/"),
        *((CONTEXT | {name}, name) for name in ("frontend/.env", "frontend/node_modules/x/a.js")),
        *((CONTEXT | {name}, name) for name in ("frontend/dist/a.js", "frontend/coverage/a")),
        *((CONTEXT | {name}, name) for name in ("backend/canary.txt", "zz-0f0f0f/canary.txt")),
    ],
)
def test_a77_web_context_is_frontend_without_closed_paths_and_deploy_web(
    names: set[str], problem: str
) -> None:
    """A77, первая строка: в контексте web — `frontend/` без `node_modules`, `dist`, `coverage`
    и `.env*`, и `deploy/web/`; файл вне открытого или в закрытом — красное с его именем."""
    text = " ".join(web.context_problems(names))
    assert (problem in text) if problem else not text, text


@pytest.mark.parametrize(
    ("extra", "hits", "problem"),
    [
        (set(), [], ""),
        ({"usr/share/nginx/html/50x.html"}, [], "50x.html"),
        ({"etc/nginx/conf.d/default.conf"}, [], "default.conf"),
        *(({name}, [], name) for name in ("build/node_modules/a.js", "build/src/main.tsx")),
        ({"build/dist/assets/index-AbCd1234.js.map"}, [], "build/dist/assets/index-AbCd1234"),
        (set(), ["etc/nginx/conf.d/web.conf"], "etc/nginx/conf.d/web.conf"),
    ],
)
def test_a77_web_image_has_exactly_dist_and_our_conf(
    extra: set[str], hits: list[str], problem: str
) -> None:
    """A77, первая и вторая строки: корень сайта — ровно файлы `dist/`, в `conf.d` — только наш
    файл; пакеты, исходники, карта или метка где угодно в образе — красное с именем файла."""
    scan = SimpleNamespace(names=IMAGE | extra, hits=hits, envs=[])
    text = " ".join(web.image_problems(scan, DIST))
    assert (problem in text) if problem else not text, text


def test_a82_listening_ports_are_read_from_proc_net_tcp() -> None:
    """A82, первая строка: слушающие порты — строки `/proc/net/tcp*` в состоянии 0A; соединения
    и заголовок не в счёт."""
    tcp = (
        "  sl  local_address rem_address   st tx_queue\n"
        "   0: 00000000:1F90 00000000:0000 0A 00000000:00000000\n"
        "   1: 0100007F:1F90 0100007F:C350 01 00000000:00000000\n"
        "   0: 00000000000000000000000000000000:0050 00000000000000000000000000000000:0000 0A\n"
    )
    assert web.listening(tcp) == {8080, 80}


OK = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
NOT_FOUND = (
    b"HTTP/1.1 404 Not Found\r\nX-Frame-Options: DENY\r\nX-Frame-Options: DENY\r\n"
    b"Content-Length: 4\r\nConnection: close\r\n\r\nnope"
)


def reply(listener: socket.socket, raw: bytes, sent: list[bytes]) -> None:
    """Один ответ `raw` на первое соединение; запрос — в `sent`."""
    connection, _ = listener.accept()
    with connection:
        sent.append(connection.recv(4096))
        connection.sendall(raw)


@contextmanager
def answered(raw: bytes = OK, sent: list[bytes] | None = None) -> Iterator[str]:
    """Адрес на петле, где ждёт один ответ `raw`; после блока порт закрыт."""
    with socket.socket() as listener:
        listener.settimeout(10)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        answer = threading.Thread(target=reply, args=(listener, raw, [] if sent is None else sent))
        answer.start()
        yield f"http://127.0.0.1:{listener.getsockname()[1]}/"
        answer.join()


def test_a82_page_gives_the_code_or_the_failure_name() -> None:
    """A82, первая строка: страница с машины — код ответа; закрытый порт — имя сбоя строкой, а не
    исключение шага (L88)."""
    with answered() as url:
        assert web.page(url) == 200
    assert str(web.page(url)).startswith("URLError: ")


@pytest.mark.parametrize(
    ("raw", "code", "body"), [(OK, 200, b""), (NOT_FOUND, 404, b"nope")], ids=["200", "404"]
)
def test_a78_fetch_gives_the_code_every_header_value_and_the_body(
    raw: bytes, code: int, body: bytes
) -> None:
    """A78, A79, A84: ответ web с машины — код, все значения заголовков и тело; 404 — тоже ответ;
    заголовки и тело запроса уходят как заданы; закрытый порт — красное с именем сбоя (L88)."""
    sent: list[bytes] = []
    with answered(raw, sent) as url:
        got = web.fetch(url, {"X-Forwarded-For": web.CLIENT}, b"xyz")
    assert (got[0], got[2]) == (code, body)
    assert sent[0].startswith(b"POST /") and b"\r\nContent-Length: 3\r\n" in sent[0]
    assert f"\r\nX-Forwarded-For: {web.CLIENT}\r\n".encode() in sent[0]
    assert got[1].get_all("X-Frame-Options") == (["DENY", "DENY"] if code == 404 else None)
    with pytest.raises(web.StepError, match="URLError"):
        web.fetch(url)


BASE, PAGE, ASSET = "http://127.0.0.1:18100", "<!doctype html>\n", "/assets/a.js"
UNSAFE = "script-src 'self' 'unsafe-inline'"
Site = dict[str, tuple[int, dict[str, list[str]], bytes]]


def good_web() -> Site:
    """Ответы исправного web: страница по двум путям, файл сборки и `/api/` (A78–A80)."""
    common = {name: [value] for name, value in (web.SECURITY | {"Server": "nginx"}).items()}
    page = common | {"Cache-Control": [web.PAGE_CACHE], "Content-Security-Policy": [web.CSP]}
    page |= {"Cross-Origin-Opener-Policy": ["same-origin"]}
    asset = (200, common | {"Cache-Control": [web.ASSET_CACHE]}, b"js")
    api = common | {"Cache-Control": ["no-store"], "X-Request-ID": [web.REQUEST_ID]}
    site = {path: (200, dict(page), PAGE.encode()) for path in web.PAGES} | {ASSET: asset}
    return site | {"/api/health": (200, dict(api), b"{}"), "/api/nope": (403, dict(api), b"{}")}


def message(headers: dict[str, list[str]]) -> Message:
    """Заголовки ответа: у имени — все его значения по порядку."""
    found = Message()
    for name, value in ((name, v) for name, values in headers.items() for v in values):
        found[name] = value
    return found


class Cat:
    """Подставной исполнитель Docker: `cat dist/index.html` стадии сборки отдаёт `PAGE`."""

    def __init__(self) -> None:
        self.containers: list[str] = []
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str, **kw: object) -> str:
        self.calls.append(args)
        return PAGE


def web_run(site: Site, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Прогон на подставном web: порт прогона, файлы `dist/`, ответы `site` вместо сети."""

    def fetch(url: str, *request: object) -> tuple[int, Message, bytes]:
        code, headers, body = site[url.removeprefix(BASE)]
        return code, message(headers), body

    monkeypatch.setattr(web, "fetch", fetch)
    facts = {"slot": (18100, "172.16.0.0/28"), "dist": {"index.html", ASSET[1:]}}
    return SimpleNamespace(name="ai-geo-audit-oracle-test", facts=facts, docker=Cat())


def test_a78_a80_web_steps_are_green_on_a_good_web(monkeypatch: pytest.MonkeyPatch) -> None:
    """A78, A79 (первая строка), A80: на исправном web шаги зелёные; страница сверяется с
    `index.html` стадии сборки — `cat` разовым контейнером без сети, контейнер в учёте уборки; нет
    файлов `assets/` в `dist/` — красное словами (L88)."""
    run = web_run(good_web(), monkeypatch)
    for step in (web.step_web_cache, web.step_web_headers, web.step_web_csp):
        assert step(run)
    flags = ("--rm", "--name", f"{run.name}-web-dist", "--network", "none", "--entrypoint", "cat")
    assert run.docker.calls == [("run", *flags, f"{run.name}-web-build:oracle", "dist/index.html")]
    assert run.docker.containers == [f"{run.name}-web-dist"]
    run.facts["dist"] = {"index.html"}
    with pytest.raises(web.StepError, match="нет файлов assets/"):
        web.step_web_headers(run)


def put(path: str, name: str, *values: str) -> Callable[[Site], object]:
    """Порча web: у ответа `path` заголовок `name` — ровно `values`, без значений — снят."""
    return lambda site: site[path][1].update({name: list(values)})


SITE_SPOILS = [
    ("снят try_files", "cache", lambda s: s.update({"/x/y": (404, {}, b"")}), "/x/y: код 404"),
    ("не та страница", "cache", lambda s: s.update({"/": (200, s["/"][1], b"")}), "/: не index"),
    ("два Cache-Control", "cache", put(ASSET, "Cache-Control", "a", "b"), "Control — ['a', 'b']"),
    ("без include", "headers", put(ASSET, "X-Frame-Options"), f"{ASSET}: X-Frame-Options — []"),
    ("версия nginx", "headers", put("/", "Server", "nginx/1.30.6"), "Server — ['nginx/1.30.6']"),
    ("номер заменён", "headers", put("/api/nope", "X-Request-ID", "web-1"), "ID — ['web-1']"),
    ("CSP снят", "csp", put("/x/y", "Content-Security-Policy"), "/x/y: заголовков CSP 0"),
    ("unsafe-inline", "csp", put("/", "Content-Security-Policy", UNSAFE), "script-src — 'self' 'u"),
    ("нет COOP", "csp", put("/x/y", "Cross-Origin-Opener-Policy"), "/x/y: Cross-Origin-Opener"),
]


@pytest.mark.parametrize(
    ("spoil", "step", "change", "why"), SITE_SPOILS, ids=[s[0] for s in SITE_SPOILS]
)
def test_a78_a80_web_step_on_a_spoiled_web_is_red_with_the_reason(
    monkeypatch: pytest.MonkeyPatch,
    spoil: str,
    step: str,
    change: Callable[[Site], object],
    why: str,
) -> None:
    """A78, A79 (первая строка), A80: порча web — красный шаг с причиной: `/x/y` — 404 без
    `try_files`, страница не из `dist/`, два `Cache-Control` у файла сборки, заголовок защиты
    пропал или `Server` с версией, у `/api/` чужой номер запроса, CSP нет или директива
    ослаблена, нет `Cross-Origin-Opener-Policy`."""
    site = good_web()
    change(site)
    with pytest.raises(web.StepError) as failure:
        getattr(web, f"step_web_{step}")(web_run(site, monkeypatch))
    assert why in str(failure.value), spoil


@pytest.mark.parametrize("spoil", ["", "страница nginx", "без no-store"])
def test_a84_refusal_is_the_proxy_sample_with_no_store(spoil: str) -> None:
    """A84, A79: отказ web равен образцу раздела «прокси» — код, тип и тело — и несёт `no-store` и
    заголовки защиты; страница nginx или отказ без `no-store` — красное."""
    proxy = json.loads((dbtools.ROOT / web.CONTRACT).read_text("utf-8"))["proxy"]
    sample = next(s for s in proxy if s["id"] == "proxy-bad-gateway")
    head = web.SECURITY | {"Server": "nginx", "Content-Type": "application/json"}
    head |= {} if spoil == "без no-store" else {"Cache-Control": "no-store"}
    body = b"<html>nginx</html>" if spoil else json.dumps(sample["body"]).encode()
    got = (502, message({name: [value] for name, value in head.items()}), body)
    found = " ".join(web.refusal_problems("502", got, sample))
    assert (spoil == "") is (found == ""), found


HEALTH = "2026-11-23T12:00:00+00:00 GET / 200 0.000 -"


def line(request: str) -> str:
    """Своя строка журнала web: запрос с кодом, время ответа, адрес клиента шага отказов (A83)."""
    return f"2026-11-23T12:00:31+00:00 {request} 30.004 {web.CLIENT}"


ERROR = '2026/11/23 12:00:31 [error] 29#29: *7 request: "GET /api/health?token=MARK HTTP/1.1"'
JOURNAL_SPOILS = [
    ("", lambda lines: lines, ""),
    ("ошибка с меткой", lambda lines: [*lines, ERROR], "метка в журнале web: 2026/11/23"),
    ("формат образа", lambda lines: [HEALTH], "свои строки — [], а не"),
    ("путь после try_files", lambda lines: [*lines[:2], line("GET /index.html 200")], "GET /in"),
    ("строки нет", lambda lines: lines[1:], "свои строки — ['GET /x/y 200']"),
    ("не по маске", lambda lines: [s.replace(" 30.004", " 30") for s in lines], "не по маске: 20"),
]


@pytest.mark.parametrize(
    ("spoil", "change", "why"), JOURNAL_SPOILS, ids=[s[0] for s in JOURNAL_SPOILS]
)
def test_a83_journal_has_no_mark_and_a_line_per_answer_besides_health_checks(
    spoil: str, change: Callable[[list[str]], list[str]], why: str
) -> None:
    """A83, вторая строка: метки нет в журнале web; своих строк — по одной на ответ, по маске, путь
    как прислан; строка проверки здоровья `GET /` — не в счёт. Метка в строке ошибок, формат образа,
    путь после `try_files`, пропавшая строка, время без трёх знаков — красное с причиной."""
    log = "\n".join(change([line("GET /api/health 504"), HEALTH, line("GET /x/y 200")]))
    found = " ".join(web.journal_problems(log, "MARK", ["GET /api/health 504", "GET /x/y 200"]))
    assert (why in found) if why else not found, found


def test_a81_a84_failed_api_steps_restore_api_name_logs_and_remove_the_squat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A84: сбой паузы api — красное шага, а api снят с паузы, запущен и ждёт healthy (L88);
    A81: сбой `up --force-recreate` — красное с хвостами журналов migrate и api (L89), разовый
    контейнер на прежнем адресе api — в учёте уборки и убран."""
    calls: list[str] = []

    def compose(run: object, *args: str, **kw: object) -> str:
        calls.append(args[0])
        if args[0] in ("pause", "up"):
            raise web.StepError(f"{args[0]} — код 1")
        return ""

    nets = {"NetworkSettings": {"Networks": {"front": {"IPAddress": "172.16.0.3"}}}}
    monkeypatch.setattr(web, "compose", compose)
    monkeypatch.setattr(web, "fetch", lambda *args, **kw: (200, Message(), b""))
    monkeypatch.setattr(web, "inspect", lambda *args: nets)
    monkeypatch.setattr(web, "wait_health", lambda *args: calls.append("healthy"))
    monkeypatch.setattr(web, "service_log", lambda run, service: f"журнал {service}")
    facts = {"slot": (18100, "172.16.0.0/28")}
    run = SimpleNamespace(
        name="o", label="MARK", image="i", copy=dbtools.ROOT, facts=facts, docker=Cat()
    )
    with pytest.raises(web.StepError, match="pause — код 1"):
        web.step_api_refusals(run)
    assert calls == ["pause", "unpause", "start", "healthy"]
    with pytest.raises(web.StepError, match="up — код 1; журнал migrate; журнал api"):
        web.step_api_recreate(run)
    assert run.docker.containers == ["o-squat"] and run.docker.calls[-1] == ("rm", "-f", "o-squat")
