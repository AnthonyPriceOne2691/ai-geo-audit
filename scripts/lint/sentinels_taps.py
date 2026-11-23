"""Стражи исполнения, часть: перехватчики и состояние прогона (CQG §2.7c).

Часть `run_sentinels.py` — разрезан по планке 300 строк (Delivery §9.1a п.5). Здесь
вопрос «что тест отправил в код и что код записал»: метки, перехват логгера, запросов
теста, кадров WebSocket и пула. Вердикты и итог остались во входном модуле.
Всё, что касается чужого (значение лога, тело, кадр, API библиотеки), — под `guarded`:
сбой стража — его отказ словами, а не исключение в коде под тестом (`cqg@3.24`).
"""

from __future__ import annotations

import logging
import os
import re
import sys

#: Секретное имя переменной — по ЧАСТИ имени, а не списком имён.
SECRET_NAME = re.compile(r"(?:^|_)(?:SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIALS?|"
                         r"API_?KEY|PRIVATE_?KEY|ACCESS_?KEY)(?:_|$)", re.I)
#: Короче — значение совпадает случайно: «password» есть в честном логе.
MIN_SECRET = 12
#: Свободный текст запроса: есть пробел и не короче этого. Идентификатор и слаг
#: пробела не несут — их логировать законно (§2.7), их страж не трогает.
MIN_TEXT = 12
#: Ищется НАЧАЛО текста: лог с `text[:50]` — та же утечка, а полного значения в нём
#: нет. 24 знака свободного текста с пробелом в честном логе случайно не встречаются.
PREFIX = 24


class Watch:
    """Метки сессии и перехваченное. Один экземпляр на прогон."""

    def __init__(self) -> None:
        self.marks: dict[str, str] = {}     # значение → откуда метка
        self.records: list[tuple] = []      # (логгер, метод, args, extra, исключение)
        self.taken: dict[int, str] = {}     # id записи пула → тест, взявший её
        self.local_urls: set[str] = set()   # запросы теста к приложению
        self.leaks: list[str] = []
        self.faults: list[tuple[str, str]] = []  # (тест или "" вне теста, «где — Тип: текст»)
        self.seen = self.text_tests = self.pool = self.lost = self.streams = self.refused = 0
        self.phase = self.nodeid = ""
        self._re: re.Pattern | None = None

    def record_fault(self, where: str, exc: Exception) -> None:
        """Сбой самого стража — отказ у текущего теста, вне теста — у прогона."""
        self.faults.append((self.nodeid, f"{where} — {type(exc).__name__}: {as_text(exc)}"))

    def mark(self, value: str, source: str) -> None:
        if value and value not in self.marks:
            self.marks[value] = source
            self._re = None

    def found(self, text: str) -> list[str]:
        if not self.marks:
            return []
        if self._re is None:
            self._re = re.compile("|".join(map(re.escape, sorted(self.marks, key=len,
                                                                 reverse=True))))
        return sorted({self.marks[m] for m in self._re.findall(text)})


WATCH = Watch()


def as_text(value) -> str:
    """Значение записи текстом; не печатается — заглушка с именем типа. Поле с падающим
    `__str__` логгер переживает, а `str()` стража ронял им весь прогон (INTERNALERROR)."""
    try:
        return str(value)
    except Exception:  # silent-ok: заглушка вместо значения — запись судится дальше
        return f"<{type(value).__name__}: не печатается>"


def guarded(where: str):
    """Сбой стража на чужом входе — запись для отказа, а не исключение в коде под тестом
    (тест краснел бы чужим диагнозом) и не обрыв прогона."""
    def wrap(fn):
        def run(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                WATCH.record_fault(where, exc)
                return None
        return run
    return wrap


def watch_secrets() -> None:
    """Значения секретных переменных — метки. Перечитывается перед каждым вердиктом:
    conftest, фикстура и `monkeypatch.setenv` ставят их позже, чем грузится плагин,
    а секрет, поставленный внутри теста, к фазе отчёта ещё не снят."""
    for name, value in os.environ.items():
        if SECRET_NAME.search(name) and len(value) >= MIN_SECRET:
            WATCH.mark(value, f"секрет {name}")


def strings(value) -> list[str]:
    """Строковые листья JSON-тела или параметров запроса."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in strings(v)]
    return []


def mark_text(values: list[str], body) -> None:
    """Свободный текст, который тест отправил в приложение, — метка."""
    import json
    try:
        values = values + strings(json.loads(body or b"null"))
    except (ValueError, UnicodeDecodeError):  # silent-ok: не JSON — судим остальное
        values += [body] if isinstance(body, str) else []
    for v in values:
        if len(v) >= MIN_TEXT and re.search(r"\s", v.strip()):
            WATCH.mark(v[:PREFIX], "текст из запроса теста")


@guarded("установка перехвата httpx")
def wrap_httpx() -> None:
    """Запрос ТЕСТА к приложению: транспорт внутри процесса (ASGI/WSGI/TestClient).
    Исходящие запросы самого приложения к внешним API сюда не попадают."""
    try:
        import httpx
    except ImportError:  # silent-ok: нет httpx — оборачивать нечего
        return
    local = ("ASGITransport", "WSGITransport", "_TestClientTransport")
    sync_send, async_send = httpx.Client.send, httpx.AsyncClient.send

    @guarded("перехват httpx")
    def mark(client, request) -> None:
        if type(getattr(client, "_transport", None)).__name__ not in local:
            return
        WATCH.local_urls.add(str(request.url))
        try:
            body = request.content
        except httpx.RequestNotRead:    # тело потоком (генератор, multipart): чтение съело
            body, WATCH.streams = None, WATCH.streams + 1   # бы его до приложения — итог скажет
        mark_text([v for _, v in request.url.params.multi_items()], body)

    def send(self, request, **kw):
        mark(self, request)
        return sync_send(self, request, **kw)

    async def asend(self, request, **kw):
        mark(self, request)
        return await async_send(self, request, **kw)

    httpx.Client.send, httpx.AsyncClient.send = send, asend


@guarded("установка перехвата WebSocket")
def wrap_websocket() -> None:
    """Кадр WebSocket, отправленный ТЕСТОМ (`send_text`/`send_json` идут через `send`).

    Модуль тест-клиента страж НЕ импортирует сам: starlette 1.9 на этом импорте
    бросает DeprecationWarning («install httpx2»), и плагин шумел бы в каждом проекте,
    а при `-W error` ронял бы прогон (замер в CI канона, `cqg@2.74`). Оборачивается то,
    что загрузил сам проект, — зовётся на старте каждого теста, патчит один раз."""
    module = sys.modules.get("starlette.testclient")
    session = getattr(module, "WebSocketTestSession", None)
    if session is None or getattr(session.send, "contour_tap", False):
        return
    orig = session.send

    @guarded("перехват WebSocket")
    def note(message) -> None:
        if isinstance(message, dict):
            mark_text([], message.get("text"))

    def send(self, message):
        note(message)
        return orig(self, message)

    send.contour_tap = True
    session.send = send


#: Методы, которыми код пишет в лог. Перехват — ДО проверки уровня: в тестах корень
#: стоит на WARNING, и `info` с текстом пользователя до хендлеров не доходит, а в
#: проде доходит (замер на стенде: из трёх утечек на INFO не увидена ни одна).
LOG_METHODS = ("debug", "info", "warning", "warn", "error", "exception", "critical",
               "fatal", "log")


#: Обёртка компилируется С ИМЕНЕМ ФАЙЛА `logging`: `findCaller` пропускает такие
#: кадры как внутренние, и «файл:строка» записи остаётся строкой приложения. Иначе
#: каждая запись в тестах указывала бы на этот плагин (замер на стенде).
_WRAPPER = """
def method(self, *args, **kwargs):
    if getattr(depth, "n", 0) == 0:
        note(self, name, args, kwargs)
    depth.n = getattr(depth, "n", 0) + 1
    try:
        return orig(self, *args, **kwargs)
    finally:
        depth.n -= 1
"""


@guarded("перехват лога")
def note_record(logger, name: str, args: tuple, kwargs: dict) -> None:
    """Запись для стража: кто, каким методом, с чем. Кадр закрыт до вызова логгера."""
    exc = sys.exc_info()[1] if name == "exception" or kwargs.get("exc_info") else None
    who = logger.logger.name if isinstance(logger, logging.LoggerAdapter) else logger.name
    WATCH.records.append((who, name, args, kwargs.get("extra"), exc))


@guarded("установка перехвата лога")
def wrap_logging() -> None:
    """Вызов метода логгера — запись для стража; поведение логгера не меняется.
    Хендлеры приложение вправе снести (`basicConfig(force=True)`) — страж не на них.
    Считается внешний вызов: `exception` зовёт `error`, адаптер — логгер."""
    import threading
    code = compile(_WRAPPER, logging.addLevelName.__code__.co_filename, "exec")
    depth = threading.local()
    for cls in (logging.Logger, logging.LoggerAdapter):
        for name in LOG_METHODS:
            if hasattr(cls, name):          # `fatal`/`warn` есть не у всех и не везде
                ns = {"depth": depth, "name": name, "orig": getattr(cls, name),
                      "note": note_record}
                exec(code, ns)  # noqa: S102 -- тело — константа выше, не внешний ввод
                setattr(cls, name, ns["method"])


@guarded("установка перехвата пула")
def watch_pool() -> None:
    try:
        from sqlalchemy import event
        from sqlalchemy.pool import Pool
    except ImportError:  # silent-ok: нет SQLAlchemy — пула нет, итог это скажет
        return

    @guarded("перехват пула")
    def checkout(_dbapi, rec, _proxy) -> None:
        if WATCH.phase == "call":
            WATCH.taken[id(rec)] = WATCH.nodeid
            WATCH.pool += 1

    @guarded("перехват пула")
    def checkin(_dbapi, rec) -> None:
        WATCH.taken.pop(id(rec), None)

    event.listen(Pool, "checkout", checkout)
    event.listen(Pool, "checkin", checkin)
