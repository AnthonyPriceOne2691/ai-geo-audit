"""Разбор `deploy/web/nginx.conf` и сниппета заголовков без Docker (A79, вторая строка; A80; A83,
статическая часть): сниппет в каждом location со своим `add_header`, `always` у заголовков
сниппета, `server_tokens off`, директивы CSP по одной, поля журнала доступа и уровень журнала
ошибок. Разбор nginx — свой, без пакета. Заголовки и CSP сверяются с ожиданием оракула: две
записи спеки держат друг друга. `/api/` и `proxy_hide_header` — срез 3.
"""

from __future__ import annotations

import importlib
import re
import sys

import envguard
import pytest

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
_TOKEN = re.compile(r"""#[^\n]*|"([^"]*)"|'([^']*)'|([{};])|([^\s{};"'#]+)""")
Found = list[tuple[str, list[str]]]


def directives(text: str) -> Found:
    """Свой маленький разбор nginx: директива — блоки над ней через « / » и её слова. Слово — без
    пробелов и `{};`, строка — в кавычках, комментарий — до конца строки."""
    found: Found = []
    blocks: list[str] = []
    words: list[str] = []
    for token in _TOKEN.finditer(text):
        double, single, mark, word = token.groups()
        if mark == "{":
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
    return problems + [f"заголовки сниппета — {values}"] * (values != web.SECURITY)


def page_problems(conf: Found) -> list[str]:
    """A80: у страницы — CSP ровно из десяти директив спеки и `Cross-Origin-Opener-Policy`."""
    heads = [args for block, args in pick(conf, "add_header") if block == "server / location /"]
    csp = [value for name, value, *_ in heads if name == "Content-Security-Policy"]
    coop = [value for name, value, *_ in heads if name == "Cross-Origin-Opener-Policy"]
    return web.csp_problems(csp) + [f"Cross-Origin-Opener-Policy — {coop}"] * (
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


def problems(texts: dict[str, str]) -> list[str]:
    conf = directives(texts["nginx.conf"])
    tokens = pick(conf, "server_tokens")
    return [
        *include_problems(conf),
        *snippet_problems(directives(texts["security-headers.conf"])),
        *[f"server_tokens — {tokens}"] * (tokens != [("server", ["off"])]),
        *page_problems(conf),
        *log_problems(conf),
    ]


def read(name: str) -> Found:
    return directives((WEB / name).read_text(encoding="utf-8"))


def test_a79_snippet_is_included_at_server_level_and_in_each_location_with_add_header() -> None:
    """A79, вторая строка: сниппет — на уровне server и в каждом location со своим `add_header`
    (`add_header` не наследуется туда, где есть свой); `include` называет путь, куда сниппет кладёт
    `Dockerfile`. Свои `add_header` — у страницы и у файлов сборки."""
    dockerfile = (WEB / "Dockerfile").read_text(encoding="utf-8")
    assert f"\nCOPY deploy/web/security-headers.conf {SNIPPET}\n" in dockerfile
    conf = read("nginx.conf")
    assert include_problems(conf) == []
    own = {block for block, _ in pick(conf, "add_header")}
    assert own == {"server / location /", "server / location /assets/"}


def test_a79_every_snippet_header_is_always_and_server_tokens_is_off() -> None:
    """A79, вторая строка: каждый `add_header` сниппета — с `always`, заголовков ровно пять, со
    значениями спеки; `server_tokens off` — на уровне server, `on` нет нигде."""
    assert snippet_problems(read("security-headers.conf")) == []
    assert pick(read("nginx.conf"), "server_tokens") == [("server", ["off"])]


def test_a80_page_csp_has_exactly_the_ten_spec_directives_and_coop() -> None:
    """A80: CSP страницы — ровно десять директив спеки, каждая со своими источниками; у страницы
    `Cross-Origin-Opener-Policy: same-origin`."""
    assert page_problems(read("nginx.conf")) == []
    assert len(web.CSP.split(";")) == 10


def test_a83_access_log_fields_and_error_log_level() -> None:
    """A83, вторая строка, статическая часть: в формате журнала доступа — ровно поля маски, нет
    `$request`, `$request_uri`, `$args`, `$query_string`, `$http_authorization`,
    `$http_x_access_token`; путь — `$request_uri` до «?»; журнал ошибок — с уровня crit."""
    assert log_problems(read("nginx.conf")) == []


PUBLIC = 'add_header Cache-Control "public'
INCLUDE = f"include {SNIPPET};\n        {PUBLIC}"
UNSAFE = "script-src 'self' 'unsafe-inline';"
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
]


@pytest.mark.parametrize(("spoil", "name", "old", "new", "why"), SPOILS, ids=[s[0] for s in SPOILS])
def test_a79_a80_a83_spoiled_web_conf_is_red_with_the_name(
    spoil: str, name: str, old: str, new: str, why: str
) -> None:
    """A79, вторая строка; A80; A83, статическая часть: порча файла web — красное с именем
    испорченного: location без `include`, заголовок без `always`, `server_tokens on`, директива
    CSP ослабленная, пропавшая, лишняя или повторённая, формат журнала образа или со строкой
    запроса, журнал ошибок с `error`, путь со строкой запроса."""
    texts = {file: (WEB / file).read_text(encoding="utf-8") for file in FILES}
    assert problems(texts) == []
    assert texts[name].count(old) == 1, spoil
    texts[name] = texts[name].replace(old, new)
    found = problems(texts)
    assert any(why in problem for problem in found), found
