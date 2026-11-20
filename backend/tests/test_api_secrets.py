"""Секреты запроса (A48): пропуск по форме и пароль сайта из `Authorization: Basic` вычищаются из
записей любого логгера в своём запросе — и из записи перехвата; мусор в заголовках запрос не
роняет; секрет запроса не копится в общем наборе вычистки. Строже подписанного: клиент заголовком
не выбирает, что потеряют поля слоя, — их и форму записи секреты запроса не трогают."""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import sys

import api_probes as probes
import dbtools
import pytest
from starlette.types import ASGIApp, Message, Scope

from ai_geo_audit.logs import setup as log_setup

ACCESS = "ai_geo_audit.api.access"
#: Схема, пара «имя:пароль» и пароль сайта, который вычистка обязана узнать.
VARIANTS = [
    ("Basic", "user:proxy-pass-words", "proxy-pass-words"),
    ("basic", "user:proxy-pass-words", "proxy-pass-words"),
    ("Basic", "user:pass:with-colons-words", "pass:with-colons-words"),
]


def json_records(err: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in err.splitlines() if line.startswith("{")]


@pytest.mark.parametrize("fmt", ["json", "text"])
@pytest.mark.parametrize(("scheme", "pair", "password"), VARIANTS)
async def test_a48_pass_and_site_password_never_reach_the_output(
    capsys: pytest.CaptureFixture[str], fmt: str, scheme: str, pair: str, password: str
) -> None:
    """A48: проба пишет `Authorization`, пропуск и пароль сайта; вторая падает с пропуском в
    тексте. С тестовым пропуском и `Basic` (и `basic` строчными, и паролем с двоеточиями) — в
    выводе нет ни пароля, ни строки после схемы, ни пропуска, на их месте `***`: и в записи
    пробы, и в записи ERROR перехвата с трассировкой, в обоих форматах; у пароля с двоеточиями
    маскируется всё после первого."""
    encoded = base64.b64encode(pair.encode()).decode()
    sent = {"X-Access-Token": probes.PASS, "Authorization": f"{scheme} {encoded}"}
    app = probes.build()
    log_setup.setup_logging(fmt=fmt)
    async with probes.client(app) as http:
        logged = await http.get(probes.HEADERS, headers=sent)
        crashed = await http.get(probes.BOOM_PASS, headers=sent)
    out = capsys.readouterr().err
    assert (logged.status_code, crashed.status_code) == (200, 500)
    assert [value for value in (probes.PASS, encoded, password) if value in out] == []
    if fmt == "text":
        masked = [f"заголовки: {scheme} ***", "password=***", "token=***", "упал с пропуском ***"]
        assert [part for part in masked if part not in out] == []
        return
    records = json_records(out)
    probe = next(r for r in records if r["logger"] == "ai_geo_audit.api.probe")
    assert (probe["message"], probe["token"], probe["password"]) == (
        f"заголовки: {scheme} ***",
        "***",
        "***",
    )
    error = next(r for r in records if r["level"] == "ERROR")
    assert "упал с пропуском ***" in str(error["exc"])


async def test_a48_crashed_request_has_one_access_line_and_numbered_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A48: у падающей пробы — одна строка доступа: `status` — 500, `request_id` — номер из ответа;
    запись ERROR перехват пишет в контексте номера и секретов запроса."""
    sent = {"X-Access-Token": probes.PASS, "Authorization": probes.BASIC}
    app = probes.build()
    log_setup.setup_logging(fmt="json")
    async with probes.client(app) as http:
        crashed = await http.get(probes.BOOM_PASS, headers=sent)
    number = crashed.headers["x-request-id"]
    records = json_records(capsys.readouterr().err)
    access = [(r["path"], r["status"], r["request_id"]) for r in records if r["logger"] == ACCESS]
    assert access == [(probes.BOOM_PASS, 500, number)]
    errors = [r for r in records if r["level"] == "ERROR"]
    assert [(r["request_id"], probes.PASS in str(r["exc"])) for r in errors] == [(number, False)]


def asgi_scope(path: str, headers: list[tuple[bytes, bytes]]) -> Scope:
    """Область запроса ASGI: заголовки — как есть, байтами."""
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test"), *headers],
        "client": ("127.0.0.1", 40000),
        "server": ("test", 80),
    }


async def bare_call(app: ASGIApp, path: str, headers: list[tuple[bytes, bytes]]) -> Message:
    """Запрос прямо в приложение ASGI; начало ответа и его тело."""
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(asgi_scope(path, headers), receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return {"status": start["status"], "body": body}


GARBAGE = [
    [(b"authorization", b"Basic !!!")],
    [(b"authorization", b"Basic")],
    [(b"authorization", b"Basic /w==")],
    [(b"authorization", b"Basic " + base64.b64encode(b"nocolon"))],
    [(b"authorization", b"Bearer abc")],
    [(b"authorization", b"\xff\xfe")],
    [(b"x-access-token", b"\xff\xfe")],
    [(b"x-access-token", b"junk one"), (b"x-access-token", b"junk two")],
]


@pytest.mark.parametrize("headers", GARBAGE)
async def test_a48_garbage_in_both_headers_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    headers: list[tuple[bytes, bytes]],
) -> None:
    """A48: мусорный `Authorization` (`Basic !!!`, без значения, байт не UTF-8, без двоеточия,
    `Bearer`, байты `\\xff\\xfe`) и мусорный `X-Access-Token` (байты, два заголовка): здоровье —
    200, закрытая проба без пропуска — 403 образец `signed-out`; ни одного 500, у каждого запроса
    — строка доступа. Разбор обоих заголовков исключений не бросает."""
    probes.use_database(monkeypatch, dbtools.own_url())
    app = probes.build()
    log_setup.setup_logging(fmt="json")
    health = await bare_call(app, "/api/health", headers)
    closed = await bare_call(app, probes.CLOSED, headers)
    signed_out = probes.sample("signed-out")
    assert (health["status"], closed["status"]) == (200, 403)
    assert json.loads(closed["body"]) == signed_out["body"]
    records = json_records(capsys.readouterr().err)
    assert [r["status"] for r in records if r["logger"] == ACCESS] == [200, 403]


async def test_a48_only_the_pass_form_is_a_secret_and_it_does_not_pile_up(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A48: `X-Access-Token: /api/nope` и `GET /api/nope` — значение не по форме пропуска, не
    секрет: в строке доступа `path` — `/api/nope`, а не `***`. 200 запросов с разными значениями
    по форме пропуска — общий набор секретов вычистки того же размера: секрет живёт в запросе."""
    app = probes.build()
    log_setup.setup_logging(fmt="json")
    before = len(log_setup._secrets)
    async with probes.client(app) as http:
        await http.get("/api/nope", headers={"X-Access-Token": "/api/nope"})
        for n in range(200):
            token = f"eyJplaceholder.test-pass-{n}.not-a-signature"
            await http.get("/api/nope", headers={"X-Access-Token": token})
    first = next(r for r in json_records(capsys.readouterr().err) if r["logger"] == ACCESS)
    assert first["path"] == "/api/nope"
    assert len(log_setup._secrets) == before


ADDRESS = "203.0.113.7"
NUMBER = "number-words-123"
PASS = {"X-Access-Token": probes.PASS}
TEXT_RECORD = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+0000 [A-Z]+ ")


def basic(pair: str) -> str:
    return "Basic " + base64.b64encode(pair.encode()).decode()


#: Секрет, выбранный клиентом, и путь запроса: поля слоя от выбора не зависят.
CHOSEN = [
    ("basic-not-base64", {"Authorization": "Basic /api/nope"}, "/api/nope"),
    ("password-is-path", {"Authorization": basic("a:/api/nope")}, "/api/nope"),
    ("password-is-client", {"Authorization": basic(f"a:{ADDRESS}")}, "/api/nope"),
    ("password-is-number", {"Authorization": basic(f"a:{NUMBER}")}, "/api/nope"),
    ("password-quoted-field", {"Authorization": basic('a:"path"')}, "/api/nope"),
    ("pass-form-in-path", {"X-Access-Token": "eyJx.aa.bb"}, "/api/eyJx.aa.bb"),
    ("password-is-logger", {"Authorization": basic(f"a:{ACCESS}")}, "/api/nope"),
]


@pytest.mark.parametrize("fmt", ["json", "text"])
@pytest.mark.parametrize(("chosen", "path"), [c[1:] for c in CHOSEN], ids=[c[0] for c in CHOSEN])
async def test_a48_client_does_not_choose_what_layer_fields_lose(
    capsys: pytest.CaptureFixture[str], fmt: str, chosen: dict[str, str], path: str
) -> None:
    """A48, строже подписанного (находка проверяющего 2026-11-20): клиент заголовком выбирает
    секрет запроса — `Basic` не base64, пароль-путь, пароль-адрес, пароль-номер, пароль `"path"`
    с кавычками, пропуск по форме в пути, пароль-имя логгера. Поля слоя — `method`, `path`,
    `status`, `client`, `request_id`, `logger` — от выбора не зависят; JSON остаётся JSON,
    строка текста — одной строкой. Путь секретом не считается: его пишет и прокси."""
    app = probes.build()
    log_setup.setup_logging(fmt=fmt)
    async with probes.client(app, address=(ADDRESS, 40000)) as http:
        response = await http.get(path, headers=chosen | {"X-Request-ID": NUMBER})
    lines = [line for line in capsys.readouterr().err.splitlines() if line]
    assert (response.status_code, response.headers["x-request-id"]) == (403, NUMBER)
    if fmt == "json":
        records = [json.loads(line) for line in lines]
        access = [r for r in records if r.get("logger") == ACCESS]
        fields = [(r["method"], r["path"], r["status"], r["client"]) for r in access]
        assert fields == [("GET", path, 403, ADDRESS)]
        assert [r["request_id"] for r in records] == [NUMBER] * len(records)
        return
    assert [line for line in lines if not TEXT_RECORD.match(line)] == []
    access_lines = [line for line in lines if f" {ACCESS} запрос | " in line]
    assert len(access_lines) == 1, lines
    tail = access_lines[0].split(" | ", 1)[1].split(" ")
    expected = ["method=GET", f"path={path}", "status=403", f"client={ADDRESS}"]
    assert [field for field in [*expected, f"request_id={NUMBER}"] if field not in tail] == []


@pytest.mark.parametrize("fmt", ["json", "text"])
async def test_a48_config_secrets_are_cleaned_everywhere_as_before(
    capsys: pytest.CaptureFixture[str], fmt: str
) -> None:
    """A48: секреты настроек клиент не выбирает — их вычистка как раньше, везде, и в полях слоя:
    секрет настроек в пути — в строке доступа `/api/***`."""
    app = probes.build()
    log_setup.setup_logging(fmt=fmt)
    log_setup.register_secret("config-secret-words-in-path")
    async with probes.client(app) as http:
        await http.get("/api/config-secret-words-in-path")
    out = capsys.readouterr().err
    assert ("config-secret-words-in-path" in out, "/api/***" in out) == (False, True)


async def test_a48_pass_form_after_any_scheme_is_a_secret(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A48: `Authorization` со схемой не `Basic` — `Bearer` с пропуском по форме: пропуск —
    секрет запроса, в записи пробы на его месте `***`."""
    bearer = "eyJplaceholder.bearer-pass-words.not-a-signature"
    app = probes.build()
    log_setup.setup_logging(fmt="json")
    async with probes.client(app) as http:
        response = await http.get(
            probes.HEADERS, headers=PASS | {"Authorization": f"Bearer {bearer}"}
        )
    out = capsys.readouterr().err
    assert (response.status_code, bearer in out, "заголовки: Bearer ***" in out) == (
        200,
        False,
        True,
    )


@pytest.mark.parametrize("fmt", ["json", "text"])
def test_a48_config_secret_is_masked_first_even_if_overlapped(
    capsys: pytest.CaptureFixture[str], fmt: str
) -> None:
    """A48: секрет запроса пересекается с концом секрета настроек и длиннее его: секрет настроек
    маскируется первым — ни куска его в выводе; секрет запроса клиент выбирает сам."""
    log_setup.setup_logging(fmt=fmt)
    log_setup.register_secret("cfg-secret-AAAA-BBBB")
    with log_setup.request_secrets("BBBB-tail-words-longer"):
        logging.getLogger("ai_geo_audit.test").info("cfg-secret-AAAA-BBBB-tail-words-longer")
    out = capsys.readouterr().err
    assert [part for part in ("cfg-secret", "AAAA") if part in out] == []


class Carrier:
    """Объект в `extra`: в выводе он станет строкой со своим секретом."""

    def __str__(self) -> str:
        return "carrier eyJplaceholder.carried-pass-words.not-a-signature"


@pytest.mark.parametrize("fmt", ["json", "text"])
def test_a48_object_that_becomes_text_is_cleaned_too(
    capsys: pytest.CaptureFixture[str], fmt: str
) -> None:
    """A48: поле `extra` — объект; в выводе он становится строкой, и секрет запроса в ней
    вычищен, как в тексте кода."""
    log_setup.setup_logging(fmt=fmt)
    with log_setup.request_secrets("eyJplaceholder.carried-pass-words.not-a-signature"):
        logging.getLogger("ai_geo_audit.test").info("объект", extra={"carried": Carrier()})
    out = capsys.readouterr().err
    assert ("carried-pass-words" in out, "carrier ***" in out) == (False, True)


async def test_a48_value_not_in_pass_form_does_not_mask_code_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A48: значение `X-Access-Token` не по форме пропуска — не секрет и в тексте кода: открытая
    проба пишет его в журнал — там оно как есть, а не `***`."""
    app = probes.build_open_body()
    log_setup.setup_logging(fmt="json")
    async with probes.client(app) as http:
        await http.get(probes.OPEN_ECHO, headers={"X-Access-Token": "/api/nope-not-a-pass"})
    records = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line]
    echo = [r["message"] for r in records if r["logger"] == "ai_geo_audit.api.probe"]
    assert echo == ["пришло: /api/nope-not-a-pass"]


#: Пропуск, пароль сайта и секрет настроек для записей, которые не собрались или прячут секрет не в
#: значении поля (приёмка проверяющего 2026-11-20). Слова-заглушки.
TOKEN = "eyJplaceholder.leak-pass-words.not-a-signature"
SITE = "site-pass-words-1"
CONFIG_WORDS = "config-words-for-unbuilt-123"
UNBUILT_TEXT = "запись журнала не собралась"


class Unprintable:
    """Поле, которое не превращается в текст."""

    def __str__(self) -> str:
        raise ValueError("не печатается")


def missing_argument(log: logging.Logger) -> None:
    log.info("вход %s %s", TOKEN)  # noqa: PLE1206 -- нехватка аргумента — предмет проверки


UNBUILT = {
    "missing-argument": (missing_argument, "TypeError"),
    "wrong-type": (lambda log: log.info("число %d", SITE), "TypeError"),
    "config-in-bad-format": (lambda log: log.info("настройка %d", CONFIG_WORDS), "TypeError"),
    "unprintable-field": (lambda log: log.info("поле", extra={"bad": Unprintable()}), "ValueError"),
}


def err_lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [line for line in capsys.readouterr().err.splitlines() if line]


@pytest.mark.parametrize("fmt", ["json", "text"])
@pytest.mark.parametrize("case", list(UNBUILT))
def test_a48_unbuilt_record_is_one_record_of_ours_without_raw_text(
    capsys: pytest.CaptureFixture[str], fmt: str, case: str
) -> None:
    """A48, строже подписанного (приёмка проверяющего 2026-11-20): запись, которая не собралась, —
    ошибка формата сообщения или поле, которое не превращается в текст, — одна запись нашего
    формата: логгер, место вызова и тип ошибки сборки; ни сырого сообщения и аргументов, ни
    «Logging error» и «Traceback»; секретов запроса и настроек нет."""
    log_setup.setup_logging(level="INFO", fmt=fmt)
    log_setup.register_secret(CONFIG_WORDS)
    write, error = UNBUILT[case]
    with log_setup.request_context("rid-1"), log_setup.request_secrets(TOKEN, SITE):
        write(logging.getLogger("ai_geo_audit.test"))
    out = err_lines(capsys)
    assert len(out) == 1, out
    raw = (TOKEN, SITE, CONFIG_WORDS, "Logging error", "Traceback", "Arguments", "%s", "%d")
    assert [part for part in raw if part in out[0]] == []
    if fmt == "json":
        record = json.loads(out[0])
        shown = (record["logger"], record["message"], record["error"], record["request_id"])
        assert shown == ("ai_geo_audit.test", UNBUILT_TEXT, error, "rid-1")
        assert str(record["source"]).startswith("test_api_secrets.py:")
        return
    assert TEXT_RECORD.match(out[0]) and f" ai_geo_audit.test {UNBUILT_TEXT} | " in out[0]
    tail = out[0].split(" | ", 1)[1].split(" ")
    assert [f"error={error}", "request_id=rid-1"] == [
        t for t in tail if t.startswith(("error=", "re"))
    ]


class Broken(io.StringIO):
    """Поток журнала, который отказывает при записи."""

    def write(self, text: str) -> int:
        raise OSError("поток отказал")


def test_a48_failed_write_does_not_print_raw_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    """A48: поток журнала отказал при записи — обработчик не печатает сырое сообщение и аргументы
    (`Handler.handleError` пишет их мимо форматтера): в stderr одна строка нашего формата, без
    секрета и без «Logging error»."""
    log_setup.setup_logging(fmt="json")
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(Broken())
    with log_setup.request_secrets(TOKEN):
        logging.getLogger("ai_geo_audit.test").info("запись %s", TOKEN)
    out = err_lines(capsys)
    assert len(out) == 1, out
    record = json.loads(out[0])
    assert (record["message"], record["error"], TOKEN in out[0]) == (UNBUILT_TEXT, "OSError", False)


@pytest.mark.parametrize("fmt", ["json", "text"])
def test_a48_dict_keys_are_cleaned_and_odd_keys_do_not_break_the_record(
    capsys: pytest.CaptureFixture[str], fmt: str
) -> None:
    """A48: секрет запроса ключом вложенного словаря чистится как значение; ключ не-строка
    (кортеж) запись не роняет — одна запись нашего формата."""
    log_setup.setup_logging(fmt=fmt)
    with log_setup.request_secrets(TOKEN):
        logging.getLogger("ai_geo_audit.test").info(
            "ключи", extra={"seen": {TOKEN: 1}, "pair": {(1, 2): "v"}}
        )
    out = err_lines(capsys)
    assert (len(out), TOKEN in out[0], "***" in out[0]) == (1, False, True), out
    if fmt == "json":
        assert json.loads(out[0])["message"] == "ключи"


NESTED = {
    "in_dict": {"k": TOKEN},
    "in_list": [TOKEN],
    "in_tuple": (TOKEN,),
    "dict_in_list": [{"k": TOKEN}],
}


@pytest.mark.parametrize("fmt", ["json", "text"])
@pytest.mark.parametrize("field", list(NESTED))
def test_a48_request_secret_in_nested_values_is_cleaned(
    capsys: pytest.CaptureFixture[str], fmt: str, field: str
) -> None:
    """A48: секрет запроса в значении словаря, в списке, в кортеже и в словаре внутри списка — на
    его месте `***`."""
    log_setup.setup_logging(fmt=fmt)
    with log_setup.request_secrets(TOKEN):
        logging.getLogger("ai_geo_audit.test").info("вложено", extra={field: NESTED[field]})
    out = err_lines(capsys)
    assert (len(out), TOKEN in out[0], "***" in out[0]) == (1, False, True), out


def test_a48_config_secret_with_a_json_escaped_sign_in_a_list(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A48: секрет настроек со знаком, который JSON экранирует (`"`), в списке — его нет ни как
    есть, ни в экранированном виде."""
    log_setup.setup_logging(fmt="json")
    log_setup.register_secret('pa"ss-word-123')
    logging.getLogger("ai_geo_audit.test").info("список", extra={"items": ['pa"ss-word-123']})
    out = err_lines(capsys)
    assert ['pa"ss-word-123' in out[0], 'pa\\"ss-word-123' in out[0], "***" in out[0]] == [
        False,
        False,
        True,
    ]


def test_a48_pg_values_with_a_newline_in_a_list_are_cleaned(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A48: строка DETAIL Postgres с переводом строки внутри значения — в списке поля: значения
    строки нет, вид ошибки остался."""
    log_setup.setup_logging(fmt="json")
    detail = "Failing row contains (7, line\nsecret-two)."
    logging.getLogger("ai_geo_audit.test").info("список", extra={"details": [detail]})
    out = err_lines(capsys)
    assert ("secret-two" in out[0], "Failing row contains" in out[0]) == (False, True)


def test_a48_short_value_does_not_become_a_request_secret(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A48: значение короче 6 знаков секретом запроса не становится: маска съела бы слова."""
    log_setup.setup_logging(fmt="json")
    with log_setup.request_secrets("short"):
        logging.getLogger("ai_geo_audit.test").info("short words")
    assert json.loads(err_lines(capsys)[-1])["message"] == "short words"


def test_a48_empty_set_replaces_the_inherited_one(capsys: pytest.CaptureFixture[str]) -> None:
    """A48: набор запроса ставится и пустым — набор, унаследованный задачей от прошлого запроса,
    не доживает до записей этого (проба 8)."""
    log_setup.setup_logging(fmt="json")
    with log_setup.request_secrets(TOKEN), log_setup.request_secrets():
        logging.getLogger("ai_geo_audit.test").info("наследство %s", TOKEN)
    assert json.loads(err_lines(capsys)[-1])["message"] == f"наследство {TOKEN}"


@pytest.mark.parametrize("fmt", ["json", "text"])
def test_a48_config_secret_in_logger_name_is_cleaned(
    capsys: pytest.CaptureFixture[str], fmt: str
) -> None:
    """A48: секрет настроек в имени логгера — на его месте `***`: базовые поля записи чистятся до
    сборки строки, как и остальные."""
    log_setup.setup_logging(fmt=fmt)
    log_setup.register_secret(CONFIG_WORDS)
    logging.getLogger(f"ai_geo_audit.{CONFIG_WORDS}").info("имя")
    out = err_lines(capsys)
    assert (CONFIG_WORDS in out[0], "ai_geo_audit.***" in out[0]) == (False, True)


def test_a48_failed_replacement_write_does_not_break_the_logging_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A48: запись не собралась, а `stderr`, куда идёт запись-замена, отказывает — вызов журнала
    возвращается без исключения: сбой журнала не роняет код, который пишет запись."""
    log_setup.setup_logging(fmt="json")
    monkeypatch.setattr(sys, "stderr", Broken())
    missing_argument(logging.getLogger("ai_geo_audit.test"))
