"""Чистые части шагов web оракула стека без Docker (A77, A82): контекст сборки и состав образа
web, слушающие порты контейнера. Модуль `scripts/stack_oracle_web.py` лежит вне пакета.
"""

from __future__ import annotations

import importlib
import sys
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
