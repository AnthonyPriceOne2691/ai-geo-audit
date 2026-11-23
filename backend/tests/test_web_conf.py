"""Разбор `deploy/web/nginx.conf` и сниппета заголовков без Docker (A79, вторая строка; A80; A83,
статическая часть; A84): сниппет в каждом location со своим `add_header`, `always` у заголовков
сниппета, `server_tokens off`, директивы CSP по одной, поля журнала доступа и уровень журнала
ошибок; `/api/` и его отказы. Разбор nginx — свой, без пакета. Заголовки, CSP и тела отказов
сверяются с литералами спеки в этом тесте, с ними же — ожидание оракула и файл договора (L36).
"""

from __future__ import annotations

import importlib
import json
import re
import sys

import api_probes as probes
import envguard
import pytest

from ai_geo_audit.api import limits

sys.path.insert(0, str(envguard.ROOT / "scripts"))
web = importlib.import_module("stack_oracle_web")

WEB = envguard.ROOT / "deploy" / "web"
FILES = ("nginx.conf", "security-headers.conf")
#: Сниппет в образе — вне `conf.d` (A77): туда его кладёт `Dockerfile`, его называет `include`.
SNIPPET = "/etc/nginx/snippets/security-headers.conf"
#: Строка доступа (A83): поля маски — время ISO 8601, метод, путь без «?», код, время ответа, адрес
#: клиента — и переменные, которых в формате нет. Путь — `$request_uri` до «?»: `$uri` после
#: `try_files` — уже `/index.html`.
MASK = "time_iso8601 request_method sent_path status request_time http_x_forwarded_for"
FORBIDDEN = "request request_uri args query_string http_authorization http_x_access_token"
PATH_MAP = [["~^(?<sent>[^?]*)", "$sent"]]
#: Пять заголовков защиты (A79) и CSP страницы (A80) — литералами спеки, а не константами
#: оракула: одинаковая порча `nginx.conf` и оракула разом — тоже красное (L36).
SECURITY = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Resource-Policy": "same-origin",
}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)
API = "server / location /api/"
#: Отказы web на `/api/` (A84) — литералами спеки: с ними сверяются раздел «прокси» файла договора
#: и тела в `nginx.conf` (L36). `id` образца — имя location отказа.
TOO_LARGE = "Запрос слишком большой — можно не больше 1 МБ"
DOWN = {"detail": "Сервис временно недоступен — повторите позже", "kind": "unavailable"}
REFUSALS = {
    "proxy-too-large": (413, {"detail": TOO_LARGE, "kind": "too-large", "retry": False}),
    "proxy-bad-gateway": (502, DOWN | {"retry": True}),
    "proxy-gateway-timeout": (504, DOWN | {"retry": True}),
}
#: Location отказа: сниппет, `no-store`, пустой `types` — тип образца и у пути с расширением.
SHAPE = {"include": [SNIPPET], "add_header": ["Cache-Control", "no-store", "always"], "types": []}
_TOKEN = re.compile(r"""#[^\n]*|"([^"]*)"|'([^']*)'|([{};])|([^\s{};"'#]+)""")
Found = list[tuple[str, list[str]]]


def directives(text: str) -> Found:
    """Свой маленький разбор nginx: директива — блоки над ней через « / » и её слова, блок — и
    своей строкой. Слово — без пробелов и `{};`, строка — в кавычках, комментарий — до конца."""
    found: Found = []
    blocks: list[str] = []
    words: list[str] = []
    for token in _TOKEN.finditer(text):
        double, single, mark, word = token.groups()
        if mark == "{":
            found.append((" / ".join(blocks) or "http", words))
            blocks.append(" ".join(words))
        elif mark == "}":
            blocks.pop()
        elif mark == ";":
            found.append((" / ".join(blocks) or "http", words))
        elif token.lastindex:
            words.append(next(part for part in (double, single, word) if part is not None))
        words = [] if mark else words
    assert not blocks and not words, "скобки или «;» не сошлись"
    return found


def pick(found: Found, name: str) -> Found:
    """Директивы с этим именем: блок и аргументы."""
    return [(block, words[1:]) for block, words in found if words[0] == name]


def include_problems(conf: Found) -> list[str]:
    """A79: сниппет — на уровне server и в каждом блоке со своим `add_header`."""
    own = {"server"} | {block for block, _ in pick(conf, "add_header")}
    found = pick(conf, "include")
    return [
        f"{block}: нет include сниппета" for block in sorted(own) if (block, [SNIPPET]) not in found
    ]


def snippet_problems(snippet: Found) -> list[str]:
    """A79: в сниппете — только `add_header`, каждый с `always`; заголовки — пять спеки."""
    heads = {args[0]: args[1:] for _, args in pick(snippet, "add_header")}
    problems = [f"в сниппете {words[0]}" for _, words in snippet if words[0] != "add_header"]
    problems += [f"{name} — без always" for name, rest in heads.items() if rest[1:] != ["always"]]
    values = {name: rest[0] for name, rest in heads.items()}
    return problems + [f"заголовки сниппета — {values}"] * (values != SECURITY)


def page_problems(conf: Found) -> list[str]:
    """A80: у страницы — CSP ровно из десяти директив спеки и `Cross-Origin-Opener-Policy`."""
    heads = [args for block, args in pick(conf, "add_header") if block == "server / location /"]
    csp = [value for name, value, *_ in heads if name == "Content-Security-Policy"]
    coop = [value for name, value, *_ in heads if name == "Cross-Origin-Opener-Policy"]
    return web.csp_problems(csp, CSP) + [f"Cross-Origin-Opener-Policy — {coop}"] * (
        coop != ["same-origin"]
    )


def log_problems(conf: Found) -> list[str]:
    """A83: один журнал доступа на уровне server — формат из полей маски, без строки запроса и
    заголовков пропуска; путь — `$request_uri` до «?»; журнал ошибок — server, с уровня crit."""
    formats = {args[0]: " ".join(args[1:]) for _, args in pick(conf, "log_format")}
    logs, errors = pick(conf, "access_log"), pick(conf, "error_log")
    fields = [re.findall(r"\$\{?(\w+)", formats.get(args[-1], "")) for _, args in logs]
    problems = [f"в формате ${name}" for got in fields for name in FORBIDDEN.split() if name in got]
    good = [(block, " ".join(got)) for (block, _), got in zip(logs, fields, strict=True)]
    problems += [f"access_log — {logs}, поля {fields}"] * (good != [("server", MASK)])
    levels = [(block, args[1:]) for block, args in errors]
    problems += [f"error_log — {errors}"] * (levels != [("server", ["crit"])])
    cut = [words for block, words in conf if block == "map $request_uri $sent_path"]
    return problems + [f"map $request_uri $sent_path — {cut}"] * (cut != PATH_MAP)


def api_problems(conf: Found) -> list[str]:
    """A79, A83, A84: в `/api/` скрыт `nosniff` сервиса, `X-Forwarded-For` — как прислан, предел
    тела (`m` — МиБ) равен пределу сервиса из кода Ч1.3б, текст `too-large` у сервиса и у прокси
    один; 413, 502 и 504 — в location с `id` образца «прокси»: его тип, код и тело, `no-store`."""
    api = [words for block, words in conf if block == API]
    xff = [words[2:] for words in api if words[:2] == ["proxy_set_header", "X-Forwarded-For"]]
    size = [words[1] for words in api if words[0] == "client_max_body_size"]
    sizes = [int(text.strip("m")) << 20 * text.endswith("m") for text in size]
    texts = [probes.sample(kind)["body"]["detail"] for kind in ("too-large", "proxy-too-large")]
    pages = sorted(words[1:] for words in api if words[0] == "error_page")
    hidden, limit = ["proxy_hide_header", "X-Content-Type-Options"] in api, limits.BODY_LIMIT_BYTES
    problems = [
        *["/api/: нет proxy_hide_header X-Content-Type-Options"] * (not hidden),
        *[f"/api/: X-Forwarded-For — {xff}"] * (xff != [["$http_x_forwarded_for"]]),
        *[f"предел тела: web {sizes}, сервис {limit}"] * (sizes != [limit]),
        *[f"too-large: сервис «{texts[0]}», прокси «{texts[1]}»"] * (texts[0] != texts[1]),
        *[f"error_page в /api/ — {pages}"] * ([page[0] for page in pages] != ["413", "502", "504"]),
    ]
    for code, place in pages:
        sample, inside = probes.sample(place[1:]), f"server / location {place}"
        found = {words[0]: words[1:] for block, words in conf if block == inside}
        status, body = found.pop("return", ["", "null"])
        wanted = SHAPE | {"default_type": [sample["headers"]["content-type"]]}
        nested = [block for block, _ in conf if block.startswith(f"{inside} / ")]
        problems += [f"{place}: код {code}, return {status}"] * (
            {code, status} != {str(sample["status"])}
        )
        problems += [f"{place}: тело {json.loads(body)}"] * (json.loads(body) != sample["body"])
        problems += [f"{place}: {found}, в types {nested}"] * (found != wanted or bool(nested))
    return problems


def problems(texts: dict[str, str]) -> list[str]:
    conf = directives(texts["nginx.conf"])
    tokens = pick(conf, "server_tokens")
    return [
        *include_problems(conf),
        *snippet_problems(directives(texts["security-headers.conf"])),
        *[f"server_tokens — {tokens}"] * (tokens != [("server", ["off"])]),
        *page_problems(conf),
        *log_problems(conf),
        *api_problems(conf),
    ]


def read(name: str) -> Found:
    return directives((WEB / name).read_text(encoding="utf-8"))


def test_a79_snippet_is_included_at_server_level_and_in_each_location_with_add_header() -> None:
    """A79, вторая строка: сниппет — на уровне server и в каждом location со своим `add_header`
    (`add_header` не наследуется туда, где есть свой); `include` называет путь, куда сниппет кладёт
    `Dockerfile`. Свои `add_header` — у страницы, у файлов сборки и у отказов `/api/` (A84)."""
    dockerfile = (WEB / "Dockerfile").read_text(encoding="utf-8")
    assert f"\nCOPY deploy/web/security-headers.conf {SNIPPET}\n" in dockerfile
    conf = read("nginx.conf")
    assert include_problems(conf) == []
    own = {block for block, _ in pick(conf, "add_header")}
    refusals = {f"server / location @{name}" for name in REFUSALS}
    assert own == {"server / location /", "server / location /assets/"} | refusals


def test_a79_every_snippet_header_is_always_and_server_tokens_is_off() -> None:
    """A79, вторая строка: каждый `add_header` сниппета — с `always`, заголовков ровно пять, со
    значениями спеки; `server_tokens off` — на уровне server, `on` нет нигде; оракул ждёт те же
    пять (L36)."""
    assert snippet_problems(read("security-headers.conf")) == []
    assert pick(read("nginx.conf"), "server_tokens") == [("server", ["off"])]
    assert web.SECURITY == SECURITY


def test_a80_page_csp_has_exactly_the_ten_spec_directives_and_coop() -> None:
    """A80: CSP страницы — ровно десять директив спеки, каждая со своими источниками; у страницы
    `Cross-Origin-Opener-Policy: same-origin`; оракул ждёт ту же CSP (L36)."""
    assert page_problems(read("nginx.conf")) == []
    assert len(CSP.split(";")) == 10
    assert web.csp_problems([web.CSP], CSP) == []


def test_a83_access_log_fields_and_error_log_level() -> None:
    """A83, вторая строка, статическая часть: в формате журнала доступа — ровно поля маски, нет
    `$request`, `$request_uri`, `$args`, `$query_string`, `$http_authorization`,
    `$http_x_access_token`; путь — `$request_uri` до «?»; журнал ошибок — с уровня crit."""
    assert log_problems(read("nginx.conf")) == []


def test_a79_a83_a84_api_location_and_its_refusals_follow_the_spec() -> None:
    """A79, вторая строка: в `/api/` — `proxy_hide_header X-Content-Type-Options`; A83:
    `X-Forwarded-For` — как прислан; A84: `client_max_body_size` — `BODY_LIMIT_BYTES` из кода
    Ч1.3б, 1 048 576 байт; у `too-large` один текст — спеки; раздел «прокси» файла договора — три
    образца спеки по форме раздела сервиса, `nginx.conf` отдаёт их из location по `id`."""
    assert api_problems(read("nginx.conf")) == []
    proxy = probes.SAMPLES["proxy"]
    kind = {"content-type": "application/json"}
    assert {s["id"]: (s["status"], s["headers"], s["body"]) for s in proxy} == {
        name: (code, kind, body) for name, (code, body) in REFUSALS.items()
    }
    assert all(sorted(s) == ["body", "headers", "id", "status", "when"] for s in proxy)
    assert limits.BODY_LIMIT_BYTES == 1_048_576
    assert probes.sample("too-large")["body"]["detail"] == TOO_LARGE


PUBLIC = 'add_header Cache-Control "public'
INCLUDE = f"include {SNIPPET};\n        {PUBLIC}"
UNSAFE = "script-src 'self' 'unsafe-inline';"
TYPES = "types { }\n        default_type application/json;\n        return 413"
SPOILS = [
    ("location без include", FILES[0], INCLUDE, PUBLIC, "/assets/: нет include"),
    ("снят always", FILES[1], '"nosniff" always;', '"nosniff";', "X-Content-Type-Options — без"),
    ("server_tokens on", FILES[0], "tokens off;", "tokens on;", "server_tokens — [('server'"),
    ("unsafe-inline", FILES[0], "script-src 'self';", UNSAFE, "script-src — 'self' 'unsafe-in"),
    ("снят frame-ancestors", FILES[0], "; frame-ancestors 'none'", "", "нет директивы frame"),
    ("base-uri 'self'", FILES[0], "base-uri 'none'", "base-uri 'self'", "base-uri — 'self'"),
    ("лишняя директива", FILES[0], "; frame-", "; worker-src *; frame-", "лишняя директива"),
    ("повтор директивы", FILES[0], "; frame-", "; script-src *; frame-", "script-src повтор"),
    ("формат образа", FILES[0], "    access_log /dev/stdout web;\n", "", "access_log — []"),
    ("формат main", FILES[0], "stdout web;", "stdout main;", "'main'])], поля [[]]"),
    ("$request_uri", FILES[0], "d $sent_path", "d $request_uri", "в формате $request_uri"),
    ("ошибки с error", FILES[0], "stderr crit;", "stderr error;", "'/dev/stderr', 'error'"),
    ("путь с запросом", FILES[0], "[^?]*", ".*", "map $request_uri $sent_path"),
    ("снят proxy_hide_header", FILES[0], "proxy_hide_header X-Content-Type-Options;", "", "нет pr"),
    ("XFF дописан", FILES[0], "$http_x_forwarded_for;", "$proxy_add_x_forwarded_for;", "'$prox"),
    ("предел web 2 МБ", FILES[0], "size 1m;", "size 2m;", "web [2097152], сервис 1048576"),
    ("текст 413 в nginx.conf", FILES[0], "больше 1 МБ", "больше 2 МБ", "@proxy-too-large: тело"),
    ("снят types", FILES[0], TYPES, TYPES[18:], "@proxy-too-large: {'include'"),
    ("снят error_page", FILES[0], "error_page 504 @proxy-gateway-timeout;", "", "error_page в"),
]


@pytest.mark.parametrize(("spoil", "name", "old", "new", "why"), SPOILS, ids=[s[0] for s in SPOILS])
def test_a79_a80_a83_spoiled_web_conf_is_red_with_the_name(
    spoil: str, name: str, old: str, new: str, why: str
) -> None:
    """A79, вторая строка; A80; A83, статическая часть; A84: порча файла web — красное с именем
    испорченного: location без `include`, заголовок без `always`, `server_tokens on`, директива
    CSP ослабленная, пропавшая, лишняя или повторённая, формат журнала образа или со строкой
    запроса, журнал ошибок с `error`, путь со строкой запроса; в `/api/` снят `proxy_hide_header`,
    `X-Forwarded-For` дописан, предел web 2 МБ, текст отказа поправлен только тут, снят `types`."""
    texts = {file: (WEB / file).read_text(encoding="utf-8") for file in FILES}
    assert problems(texts) == []
    assert texts[name].count(old) == 1, spoil
    texts[name] = texts[name].replace(old, new)
    found = problems(texts)
    assert any(why in problem for problem in found), found
