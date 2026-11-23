"""Чистые части шагов web оракула стека без Docker (A77–A80, A82): контекст сборки и состав
образа web, слушающие порты контейнера, код страницы, ответ web с заголовками, CSP и шаги
страницы, заголовков и CSP на подставном web. Модуль `scripts/stack_oracle_web.py` лежит вне
пакета.
"""

from __future__ import annotations

import importlib
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


def reply(listener: socket.socket, raw: bytes) -> None:
    """Один ответ `raw` на первое соединение."""
    connection, _ = listener.accept()
    with connection:
        connection.recv(4096)
        connection.sendall(raw)


@contextmanager
def answered(raw: bytes = OK) -> Iterator[str]:
    """Адрес на петле, где ждёт один ответ `raw`; после блока порт закрыт."""
    with socket.socket() as listener:
        listener.settimeout(10)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        answer = threading.Thread(target=reply, args=(listener, raw))
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
    """A78, A79: ответ web с машины — код, все значения каждого заголовка и тело; 404 — тоже ответ,
    а не исключение; закрытый порт — красное шага с именем сбоя (L88, L89)."""
    with answered(raw) as url:
        got = web.fetch(url)
    assert (got[0], got[2]) == (code, body)
    assert got[1].get_all("X-Frame-Options") == (["DENY", "DENY"] if code == 404 else None)
    with pytest.raises(web.StepError, match="URLError"):
        web.fetch(url)


BASE, PAGE, ASSET = "http://127.0.0.1:18100", "<!doctype html>\n", "/assets/a.js"
UNSAFE = "script-src 'self' 'unsafe-inline'"
Site = dict[str, tuple[int, dict[str, list[str]], bytes]]


def good_web() -> Site:
    """Ответы исправного web: страница по двум путям и файл сборки (A78–A80)."""
    common = {name: [value] for name, value in (web.SECURITY | {"Server": "nginx"}).items()}
    page = common | {"Cache-Control": [web.PAGE_CACHE], "Content-Security-Policy": [web.CSP]}
    page |= {"Cross-Origin-Opener-Policy": ["same-origin"]}
    asset = (200, common | {"Cache-Control": [web.ASSET_CACHE]}, b"js")
    return {path: (200, dict(page), PAGE.encode()) for path in web.PAGES} | {ASSET: asset}


class Cat:
    """Подставной исполнитель Docker: `cat dist/index.html` стадии сборки отдаёт `PAGE`."""

    def __init__(self) -> None:
        self.containers: list[str] = []
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        return PAGE


def web_run(site: Site, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Прогон на подставном web: порт прогона, файлы `dist/`, ответы `site` вместо сети."""

    def fetch(url: str) -> tuple[int, Message, bytes]:
        code, headers, body = site[url.removeprefix(BASE)]
        message = Message()
        for name, value in ((name, v) for name, values in headers.items() for v in values):
            message[name] = value
        return code, message, body

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
    пропал или `Server` с версией, CSP нет или директива ослаблена, нет
    `Cross-Origin-Opener-Policy`."""
    site = good_web()
    change(site)
    with pytest.raises(web.StepError) as failure:
        getattr(web, f"step_web_{step}")(web_run(site, monkeypatch))
    assert why in str(failure.value), spoil
