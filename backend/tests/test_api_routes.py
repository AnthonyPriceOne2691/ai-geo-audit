"""Обход маршрутов (A52): каждый маршрут дерева приложения открыт по списку или закрыт воротами,
401 нет нигде, у каждой схемы тела — запрет лишних полей; маршрут вебсокета — находка.

Обход идёт по дереву приложения, а не по схеме API: включённые роутеры раскрывает
`fastapi.routing.iter_route_contexts`, `Mount` — сам обход (проба 3). Голый 401 договор отвечает
отказом `signed-out` — поэтому «401» обхода — это и отказ `signed-out` там, где ворота запрос
пропустили: с пропуском или на открытом пути. Ноль маршрутов — находка, а не зелёный.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import api_probes as probes
import dbtools
import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIWebSocketRoute, iter_route_contexts
from starlette.routing import BaseRoute, Mount, WebSocketRoute

from ai_geo_audit.api.app import create_app

PASS = {"X-Access-Token": probes.PASS}
PATH_PARAM = re.compile(r"\{[^}]+\}")


@dataclass
class Walk:
    """Итог обхода: находки с методом и путём, число маршрутов и запросов, пройденные пути."""

    findings: list[str] = field(default_factory=list)
    routes: int = 0
    requests: int = 0
    paths: list[str] = field(default_factory=list)


def flatten(
    routes: Sequence[BaseRoute], prefix: str = ""
) -> Iterator[tuple[str, BaseRoute, set[str]]]:
    """Маршруты дерева с полным путём и методами; `Mount` раскрывается здесь. У маршрута вебсокета
    из включённого роутера путь контекста пуст — тогда путь самого маршрута."""
    for context in iter_route_contexts(routes):
        route = context.original_route
        if isinstance(route, Mount):
            yield from flatten(route.routes, prefix + route.path)
            continue
        path = context.path or getattr(route, "path", "")
        yield prefix + path, route, set(context.methods or ())


def forbids_extra(response: httpx.Response) -> bool:
    if response.status_code != 422:
        return False
    errors = response.json().get("errors", [])
    return {"loc": ["body", "zz_unknown"], "type": "extra_forbidden"} in errors


def judge(
    where: str, response: httpx.Response, *, passed: bool, listed: bool, body: bool
) -> list[str]:
    """Находки по одному ответу."""
    found = []
    if response.status_code == 401 or "www-authenticate" in response.headers:
        found.append(f"{where}: 401")
    if response.status_code >= 500:
        found.append(f"{where}: {response.status_code}")
    refused = response.headers.get("x-access-refusal") == "signed-out"
    if refused and passed:
        found.append(f"{where}: 401 с пропуском")
    elif refused and listed:
        found.append(f"{where}: 401 без пропуска на открытом пути")
    if not passed and not listed and not refused:
        found.append(f"{where}: вне списка без пропуска — {response.status_code}, а не 403")
    if body and passed and not refused and not forbids_extra(response):
        found.append(f"{where}: тело без запрета лишних полей")
    return found


async def walk(app: FastAPI) -> Walk:
    """Каждый метод каждого маршрута — без пропуска и с тестовым пропуском; параметры пути — `1`;
    маршруту с телом — `{"zz_unknown": 1}`. Список открытых — у собранного приложения."""
    listed = dict(getattr(app.state, "open_paths", {}))
    result = Walk()
    routes = list(flatten(app.routes))
    result.routes = len(routes)
    if not routes:
        result.findings.append("ноль маршрутов")
    async with probes.client(app) as http:
        for path, route, methods in routes:
            result.paths.append(path)
            if isinstance(route, APIWebSocketRoute | WebSocketRoute):
                result.findings.append(f"WEBSOCKET {path}: вебсокет мимо ворот")
                continue
            target = PATH_PARAM.sub("1", path)
            body = getattr(route, "body_field", None) is not None
            for method in sorted(methods):
                for passed in (False, True):
                    result.requests += 1
                    response = await http.request(
                        method,
                        target,
                        headers=PASS if passed else {},
                        json={"zz_unknown": 1} if body else None,
                    )
                    result.findings += judge(
                        f"{method} {path}",
                        response,
                        passed=passed,
                        listed=path in listed,
                        body=body,
                    )
    result.findings += [
        f"список: {path} без маршрута" for path in listed if path not in result.paths
    ]
    return result


async def test_a52_every_route_of_the_app_is_listed_or_gated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A52: обход по дереву `create_app()` с подставной проверкой пропуска, в `dev` и в `prod`;
    список открытых — у собранного приложения: находок ноль — ни 401, ни `WWW-Authenticate`, ни
    500, у каждой строки списка есть маршрут. В `dev` — 4 маршрута и 14 запросов (к `GET` у схемы
    и консолей Starlette добавляет `HEAD`), в `prod` — 1 и 2 (проба 9)."""
    probes.use_database(monkeypatch, dbtools.own_url())
    dev = await walk(create_app(visitor_for=probes.known_pass))
    probes.prod_settings(monkeypatch, tmp_path)
    in_production = create_app(visitor_for=probes.known_pass)
    probes.use_database(monkeypatch, dbtools.own_url())
    prod = await walk(in_production)
    print(
        f"A52: dev — маршрутов {dev.routes}, запросов {dev.requests}; "
        f"prod — маршрутов {prod.routes}, запросов {prod.requests}"
    )
    assert (dev.findings, dev.routes, dev.requests) == ([], 4, 14)
    assert (prod.findings, prod.routes, prod.requests) == ([], 1, 2)


DEFECTS = [
    ("bearer", "GET /api/defect/bearer: 401 с пропуском"),
    ("oauth2", "GET /api/defect/oauth2: 401 с пропуском"),
    ("api-key-open", "GET /api/defect/api-key-open: 401 без пропуска на открытом пути"),
    ("raise-401", "GET /api/defect/raise-401: 401 с пропуском"),
    ("loose-body", "POST /api/defect/loose-body: тело без запрета лишних полей"),
    ("list-typo", "список: /api/heath без маршрута"),
    ("websocket", "WEBSOCKET /api/defect/websocket: вебсокет мимо ворот"),
    ("empty", "ноль маршрутов"),
]


@pytest.mark.parametrize(("name", "defect"), DEFECTS, ids=[d[0] for d in DEFECTS])
async def test_a52_each_defect_probe_is_named_exactly(
    monkeypatch: pytest.MonkeyPatch, name: str, defect: str
) -> None:
    """A52: приложения тестов с одним дефектом — `HTTPBearer()`, `OAuth2PasswordBearer`,
    `APIKeyHeader` на открытом пути (проба 5), маршрут бросает 401, тело без запрета лишних полей,
    строка списка без маршрута, маршрут вебсокета, ноль маршрутов: обход называет ровно свой
    дефект, с методом и путём."""
    probes.use_database(monkeypatch, dbtools.own_url())
    found = (await walk(probes.defect_app(name))).findings
    assert found == [defect]


async def test_a52_routes_without_defects_are_all_found_and_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A52: маршруты во включённом роутере, в роутере, включённом в роутер, внутри `Mount`, со
    скрытой схемой и с телом на базе схем запросов найдены все и без находок: без пропуска — 403
    `signed-out`, у маршрута с телом — 422 `extra_forbidden` на `["body", "zz_unknown"]`. Обход
    по схеме API не увидел бы скрытого, по `app.routes` без раскрытия — включённого и вложенного."""
    probes.use_database(monkeypatch, dbtools.own_url())
    result = await walk(probes.clean_app())
    assert result.findings == []
    assert [path for path in probes.CLEAN_PATHS if path not in result.paths] == []
