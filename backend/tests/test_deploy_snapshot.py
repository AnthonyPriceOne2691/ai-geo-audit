"""Скрипт выката, снимок соседа (Ч1.6а, A186): контейнеры не нашего проекта до и после, коды
адресов проверки на петле."""

from __future__ import annotations

import http.server
import re
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import deploy_records as rec
import pytest
from deploy_fakes import (
    URLS,
    Refused,
    changed,
    check_like,
    make_ctx,
    server_values,
    swap,
    tool,
    write_env,
)


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Корни — во временном каталоге, исполнитель и проба — подставные (L38)."""
    return make_ctx(tmp_path, monkeypatch)


def test_a186_snapshot_unchanged_neighbor(ctx) -> None:
    """A186: снимок до и после без перемен — «общих N, перезапусков 0, коды прежние; пропало 0,
    новых 0», не красное. Наш стек не входит; `StartedAt` с дробной частью 6, 8 и 9 знаков и
    миграции с узлом здоровья и без него сравниваются как есть; адреса опрошены оба раза."""
    started = [re.search(r'"StartedAt":"[^"]+\.(\d+)Z"', line) for line in rec.server_machine()]
    assert {len(found[1]) for found in started if found} == {6, 8, 9}
    write_env(ctx, server_values())
    result = check_like(ctx)
    line = "снимок соседа: общих 35, перезапусков 0, коды прежние; пропало 0, новых 0"
    assert (result.line, result.red) == (line, False) and line in ctx.out.getvalue()
    assert ctx.probe.urls == URLS.split(",") * 2


def test_a186_snapshot_divergence_is_red_with_numbers_not_names(ctx) -> None:
    """A186: у общих — перезапуск, пересоздание и смена здоровья — красное с числами, без имён;
    пропавший и новый — строкой, сами по себе не красное."""
    write_env(ctx, server_values())
    after = changed(rec.server_machine(), "nb-alpha-web-1", started="2026-11-20T09:00:00.2Z")
    after = changed(after, "nb-alpha-web-1", restarts=1)
    after = changed(after, "nb-alpha-postgres-1", health="unhealthy")
    after = changed(after, "nb-beta-api-1", id="f" * 64)
    after = swap(after, "nb-alpha-migrate-1", rec.inspect_line("nb-alpha-cron-1", "nb-alpha"))
    result = check_like(ctx, after)
    assert result.red and result.line == (
        "снимок соседа: общих 34, перезапусков 2, коды прежние; пропало 1, новых 1"
        " — красное: Id 1, StartedAt 1, RestartCount 1, здоровье 1"
    )
    assert "nb-" not in ctx.out.getvalue()
    ctx.runner.become(rec.server_machine())
    run = rec.inspect_line("nb-beta-run-1", "nb-beta")
    result = check_like(ctx, swap(rec.server_machine(), "nb-beta-migrate-1", run))
    assert (result.red, result.line.endswith("пропало 1, новых 1")) == (False, True)


def test_a186_changed_health_code_is_red(ctx) -> None:
    """A186: код адреса проверки соседа сменился между снимками — красное, адрес не назван."""
    with tool.session(ctx, "check"):
        before = tool.neighbor_snapshot(ctx, URLS)
        ctx.probe.codes["http://127.0.0.1:9102/health"] = 403
        result = tool.compare(before, tool.neighbor_snapshot(ctx, URLS))
    tail = "кодов изменилось 1; пропало 0, новых 0 — красное: коды адресов 1"
    assert result.red and result.line.endswith(tail)
    assert "9102" not in result.line


@pytest.mark.parametrize(
    "url",
    [
        "http://192.0.2.10:9101/health",
        "http://example.test:9101/health",
        "http://localhost:9101/health",
        "https://127.0.0.1:9101/health",
        "http://user:pass@127.0.0.1:9101/health",
        "http://127.0.0.1:99999/health",
        "127.0.0.1:9101/health",
    ],
)
def test_a186_address_not_on_loopback_refused_before_any_request(ctx, url: str) -> None:
    """A186: адрес проверки соседа не на петле — отказ до запроса и до первой команды; в тексте
    отказа — номер адреса, а не он сам."""
    with pytest.raises(Refused, match="адрес № 2 не на петле") as refused:
        tool.neighbor_snapshot(ctx, f"http://127.0.0.1:9101/,{url}")
    assert url not in str(refused.value) and "9101" not in str(refused.value)
    assert (ctx.probe.urls, ctx.runner.calls) == ([], [])


def test_a186_container_gone_between_listing_and_inspect_is_gone_not_failure(ctx) -> None:
    """A186: контейнер удалён между `docker ps` и `docker container inspect` — он «пропал»; сбой
    `docker ps` и чужой формат ответа — отказ словами."""
    before = tool.neighbor_snapshot(ctx, "")
    ctx.runner.vanish = {rec.cid("nb-alpha-svc-a-1"), rec.cid("ai-geo-audit-api-1")}
    assert tool.compare(before, tool.neighbor_snapshot(ctx, "")).line.endswith("пропало 1, новых 0")
    ctx.runner.become(rec.server_machine()[:1])
    ctx.runner.lines[rec.cid("nb-alpha-scaled-1")] = '{"id":"x"}'
    with pytest.raises(Refused, match="не разобран — сверить версию docker"):
        tool.neighbor_snapshot(ctx, "")
    ctx.runner.ps_code = 1
    with pytest.raises(Refused, match="docker ps: код 1"):
        tool.neighbor_snapshot(ctx, "")


@pytest.fixture
def local_server() -> Iterator[str]:
    """HTTP на петле: `/moved` — 302, остальное — 503."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302 if self.path == "/moved" else 503)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()

        def log_message(self, *_args) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_a186_health_probe_reads_code_without_following_redirects(local_server: str) -> None:
    """A186: проба адреса соседа отдаёт код как есть — по перенаправлению не идёт; закрытый
    порт — 0, «нет ответа»."""
    assert tool.http_code(local_server + "/moved") == 302
    assert tool.http_code(local_server + "/health?full=1") == 503
    assert tool.http_code("http://127.0.0.1:1/health", timeout=2) == 0
