"""Реляционный оракул ядра скрипта выката (6а-3; решение проверяющего 2026-11-20, «Открытое 6а-2»
архива 6а-1): свойства с генератором входов — hypothesis, профиль `reproducible` из conftest — для
`Files.own`, `site_name` и `health_urls`. Примерные случаи тех же функций — `test_deploy_core.py`;
здесь — отношения входа и выхода: любой вход даёт годный итог или отказ словами без значения."""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from deploy_fakes import Refused, tool
from hypothesis import given
from hypothesis import strategies as st

#: Корни — под каталогом, которого нет: ссылок нет, путь судится по написанию (ссылки и настоящие
#: каталоги — примерные случаи A186 в `test_deploy_core.py`).
BASE = Path("/ai-geo-audit-relations-absent")
PATHS = tool.Paths(
    service=BASE / "srv/ai-geo-audit",
    backups=BASE / "srv/ai-geo-audit-backups",
    etc=BASE / "etc/ai-geo-audit",
)
FILES = tool.Files(PATHS.roots)
OUTSIDE = "путь вне своих корней: " + ", ".join(map(str, PATHS.roots))
STARTS = [*PATHS.roots, BASE / "srv", BASE / "etc", BASE, BASE / "srv/nb-alpha"]
PARTS = [
    "a",
    ".deploy",
    "x.dump",
    "..",
    ".",
    "ai-geo-audit",
    "ai-geo-audit-backups",
    "ai-geo-audit-old",
    "srv",
    "etc",
]
PATHS_IN = st.builds(
    lambda start, parts: os.path.join(start, *parts),
    st.sampled_from(STARTS),
    st.lists(st.sampled_from(PARTS), max_size=6),
)


def attempt[T](call: Callable[[], T]) -> tuple[T | None, str]:
    """Итог вызова и текст отказа: принято — итог и пустая строка, отказ — `None` и текст."""
    try:
        return call(), ""
    except Refused as exc:
        return None, str(exc)


def refusal(call: Callable[[], object]) -> str:
    """Текст отказа пробного входа; принятый вход — пустая строка (тест ниже краснеет)."""
    return attempt(call)[1]


def by_spelling(path: str, *, root_itself: bool) -> Path | None:
    """Ожидание по написанию: нормализованный путь под своим корнем — он сам, сам корень — только с
    `root_itself`; иначе отказ (`None`)."""
    norm = os.path.normpath(os.path.abspath(path))
    for root in map(str, PATHS.roots):
        if norm.startswith(root + os.sep) or (root_itself and norm == root):
            return Path(norm)
    return None


@given(path=PATHS_IN, root_itself=st.booleans())
def test_a186_own_is_under_own_root_or_one_refusal(path: str, root_itself: bool) -> None:
    """A186 (реляционный): любой путь — под своим корнем по написанию, в том числе через `..`, или
    отказ одним текстом без самого пути; повтор `own` на итоге — тот же путь; `root_itself` итог
    не меняет, а только снимает отказ на самом корне."""
    assert not BASE.exists()
    expected = by_spelling(path, root_itself=root_itself)
    if expected is None:
        with pytest.raises(Refused) as refused:
            FILES.own(Path(path), root_itself=root_itself)
        assert str(refused.value) == OUTSIDE
        return
    got = FILES.own(Path(path), root_itself=root_itself)
    assert got == expected
    assert FILES.own(got, root_itself=root_itself) == got
    assert FILES.own(Path(path), root_itself=True) == got


LABEL = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?")
DOMAINS = st.lists(
    st.from_regex(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?", fullmatch=True), min_size=1, max_size=5
).map(".".join)
SHORT_DOMAINS = st.lists(
    st.from_regex(r"[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?", fullmatch=True), min_size=1, max_size=4
).map(".".join)
#: Отказы имени сайта — по пробе на правило, сняты с самой функции: любой другой текст отказа
#: значит, что в отказ попал вход.
SITE_PROBES = ["", "*", "a.", "a/b", "c" * 254, "b" * 64, "a_b"]
SITE_REFUSALS = {refusal(lambda probe=probe: tool.site_name(probe)) for probe in SITE_PROBES}
BAD_SIGNS = [
    ("*", "звёздочка"),
    ("/", "«/» и пробелы"),
    (" ", "«/» и пробелы"),
    ("\t", "«/» и пробелы"),
    (" ", "«/» и пробелы"),
    ("_", "латинские буквы"),
    (":", "латинские буквы"),
    ("é", "латинские буквы"),
]


def test_a184_site_refusals_one_text_per_rule() -> None:
    """A184: пробы правил имени сайта — семь разных отказов, ни одна не принята."""
    assert len(SITE_REFUSALS) == len(SITE_PROBES)
    assert "" not in SITE_REFUSALS


@given(domain=DOMAINS, data=st.data())
def test_a184_good_domain_gives_site_name_in_lowercase(domain: str, data: st.DataObject) -> None:
    """A184 (реляционный): домен из годных меток — `ai-geo-audit.<домен>`, пока имя не длиннее
    253 знаков, иначе отказ по длине; регистр букв входа итог не меняет."""
    name = f"ai-geo-audit.{domain}"
    flips = data.draw(st.lists(st.booleans(), min_size=len(domain), max_size=len(domain)))
    mixed = "".join(c.upper() if flip else c for c, flip in zip(domain, flips, strict=True))
    if len(name) > 253:
        with pytest.raises(Refused, match="длиннее 253"):
            tool.site_name(mixed)
        return
    assert tool.site_name(domain) == name
    assert tool.site_name(mixed) == name


@given(domain=SHORT_DOMAINS, bad=st.sampled_from(BAD_SIGNS), data=st.data())
def test_a184_one_bad_sign_gives_its_refusal(
    domain: str, bad: tuple[str, str], data: st.DataObject
) -> None:
    """A184 (реляционный): в годный домен вставлен один негодный знак — отказ по его правилу,
    точка в конце — свой отказ; текст отказа — постоянный, без значения."""
    sign, rule = bad
    at = data.draw(st.integers(0, len(domain)))
    with pytest.raises(Refused, match=rule) as refused:
        tool.site_name(domain[:at] + sign + domain[at:])
    assert str(refused.value) in SITE_REFUSALS
    with pytest.raises(Refused, match="точка в конце"):
        tool.site_name(domain + ".")


@given(text=st.text(max_size=300))
def test_a184_any_text_gives_a_good_name_or_a_fixed_refusal(text: str) -> None:
    """A184 (реляционный): любой текст — имя по правилам (строчное, `ai-geo-audit.` в начале, не
    длиннее 253, метки до 63 знаков из латиницы, цифр и дефиса) или отказ одним из постоянных
    текстов: входа в отказе нет."""
    name, refused = attempt(lambda: tool.site_name(text))
    if refused:
        assert refused in SITE_REFUSALS
        return
    assert name is not None
    assert name == f"ai-geo-audit.{text}".lower()
    assert len(name) <= 253
    assert all(len(label) <= 63 and LABEL.fullmatch(label) for label in name.split("."))


HOSTS = ["127.0.0.1", "127.1.2.3", "[::1]"]
GOOD_URLS = st.builds(
    lambda host, port, path: f"http://{host}:{port}{path}",
    st.sampled_from(HOSTS),
    st.integers(1, 65535),
    st.from_regex(r"(/[a-z0-9._-]{1,8}){0,3}/?", fullmatch=True),
)
GAPS = [",", ", ", " ,", " , ", ",,", ", ,", ",\t"]
#: Учётные данные в адресе — во всех формах, и с пустым именем пользователя: `:p@`, `@`, `:@`
#: (уточнение третьей проверяющей 2026-11-27). Адрес собирается из частей — в исходнике нет адреса
#: с паролем (A122).
USERINFO = ["u@", "u:p@", ":p@", "@", ":@"]
BAD_IPV6 = ["http://[::1/", "http://[x]:1/"]
BAD_URLS = st.one_of(
    st.builds(lambda h, p: f"https://{h}:{p}/", st.sampled_from(HOSTS), st.integers(1, 65535)),
    st.builds(
        lambda h, p: f"http://{h}:{p}/",
        st.sampled_from(["198.51.100.7", "example.test", "localhost", "10.0.0.1"]),
        st.integers(1, 65535),
    ),
    st.builds(
        lambda u, h, p: f"http://{u}{h}:{p}/",
        st.sampled_from(USERINFO),
        st.sampled_from(HOSTS),
        st.integers(1, 65535),
    ),
    st.builds(lambda h: f"http://{h}:0/", st.sampled_from(HOSTS)),
    st.builds(lambda h: f"http://{h}:70000/", st.sampled_from(HOSTS)),
    st.sampled_from(BAD_IPV6),
)
#: Отказ по номеру адреса — снят с самой функции на первом адресе.
FIRST = refusal(lambda: tool.health_urls("https://127.0.0.1:1/"))
NUMBERED = re.compile(re.escape(FIRST).replace("№ 1", r"№ [1-9]\d*"))


def numbered(place: int) -> str:
    return FIRST.replace("№ 1", f"№ {place}")


def joined(urls: list[str], data: st.DataObject) -> str:
    """Адреса через запятые с пустыми звеньями и пробелами, и по краям."""
    gaps = data.draw(st.lists(st.sampled_from(GAPS), min_size=len(urls), max_size=len(urls)))
    head = data.draw(st.sampled_from(["", " ", ","]))
    return head + "".join(url + gap for url, gap in zip(urls, gaps, strict=True))


@given(urls=st.lists(GOOD_URLS, max_size=5), data=st.data())
def test_a186_health_urls_join_and_split_back(urls: list[str], data: st.DataObject) -> None:
    """A186 (реляционный): адреса на петле, склеенные запятыми с пустыми звеньями и пробелами, —
    тот же список по порядку; склейка двух строк — сумма их списков."""
    assert FIRST.startswith("NEIGHBOR_HEALTH_URLS: адрес № 1 ")
    assert tool.health_urls(joined(urls, data)) == urls
    cut = data.draw(st.integers(0, len(urls)))
    left, right = joined(urls[:cut], data), joined(urls[cut:], data)
    assert tool.health_urls(f"{left},{right}") == tool.health_urls(left) + tool.health_urls(right)


@given(urls=st.lists(GOOD_URLS, max_size=4), bad=BAD_URLS, data=st.data())
def test_a186_bad_health_url_at_place_k_refused_by_number(
    urls: list[str], bad: str, data: st.DataObject
) -> None:
    """A186 (реляционный): один негодный адрес на месте k среди годных — не http, не петля,
    учётные данные в любой форме, порт 0 или вне, кривая запись IPv6 — отказ «адрес № k»
    постоянным текстом, без самого адреса."""
    at = data.draw(st.integers(0, len(urls)))
    with pytest.raises(Refused) as refused:
        tool.health_urls(", ".join([*urls[:at], bad, *urls[at:]]))
    assert str(refused.value) == numbered(at + 1)


@given(text=st.text(max_size=200))
def test_a186_any_text_gives_loopback_urls_or_a_numbered_refusal(text: str) -> None:
    """A186 (реляционный): любой текст — список адресов http на петле без учётных данных или
    отказ «адрес № k» постоянным текстом; другого исхода — сбоя разбора — нет."""
    urls, refused = attempt(lambda: tool.health_urls(text))
    if refused:
        assert NUMBERED.fullmatch(refused)
        return
    for url in urls or []:
        parts = urlsplit(url)
        assert parts.scheme == "http"
        assert "@" not in parts.netloc
        assert ipaddress.ip_address(parts.hostname or "").is_loopback


@pytest.mark.parametrize(
    "userinfo", USERINFO, ids=["имя", "имя-пароль", "пароль", "пусто", "пустой-пароль"]
)
def test_a186_health_url_with_any_userinfo_refused(userinfo: str) -> None:
    """A186: учётные данные в адресе проверки соседа — в любой форме, и с пустым именем
    пользователя (`:p@`, `@`, `:@`), — отказ «адрес № 1» без значения: порча «`username or
    password`» иначе живёт на `@` и `:@` (уточнение третьей проверяющей 2026-11-27)."""
    with pytest.raises(Refused) as refused:
        tool.health_urls(f"http://{userinfo}127.0.0.1:18181/")
    assert str(refused.value) == numbered(1)


@pytest.mark.parametrize("raw", BAD_IPV6, ids=["скобка", "не-адрес"])
def test_a186_bad_ipv6_spelling_is_a_refusal_not_a_failure(raw: str) -> None:
    """A186: кривая запись IPv6 — отказ «адрес № 2» словами, а не сбой разбора: `ValueError` из
    `urlsplit` несёт значение и обходит отказ (проба 2026-11-27T09:32Z)."""
    with pytest.raises(Refused) as refused:
        tool.health_urls(f"http://127.0.0.1:18181/, {raw}")
    assert str(refused.value) == numbered(2)


def test_a186_health_url_without_host_is_refused() -> None:
    """A186: адрес без узла (`http://:1/`) — негодная запись, отказ «адрес № 2», как у кривого
    IPv6: порча «узел по умолчанию — петля» (`hostname or "127.0.0.1"`) без этого случая живёт
    (приёмка третьей проверяющей, решение ведущей 2026-11-27)."""
    with pytest.raises(Refused) as refused:
        tool.health_urls("http://127.0.0.1:18181/, http://:1/")
    assert str(refused.value) == numbered(2)
