"""Правила защиты стека (A86, A87): права Linux, корень только для чтения, постоянный адрес web и
сети. Перечень, порядок и вывод — в `stack_rules`: там коды 17–23 ведут сюда.

Вход — `Stack` правил стека; модуль берёт его только для типов, поэтому `stack_rules` импортирует
отсюда без круга. Здесь же разбор постоянного адреса: его делят `trusted-proxy`,
`web-address` и `trusted-is-web`.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai_geo_audit.ops.stack_rules import Finding, Stack

#: Единственный доверенный прокси api — адрес web из переменной сервера.
WEB_ADDRESS = "WEB_ADDRESS"
STRICT_REF = re.compile(r"\$\{([A-Za-z_]\w*):\?[^}]+\}")
NO_NEW_PRIVILEGES = "no-new-privileges:true"
#: Корень на запись — поимённо и с причиной (A87). Пуст: у db запись — в tmpfs (слово владельца
#: 2026-11-23T14:12:15Z); механизм остаётся — «не в списке исключений».
WRITABLE_ROOT: dict[str, str] = {}
#: Внутренние сети — без выхода наружу (A86); обычная сеть — только у web.
INTERNAL_NETS = frozenset({"data", "front"})


def one_address(text: str) -> bool:
    try:
        ipaddress.ip_address(text.strip())
    except ValueError:
        return False
    return True


def address(value: object) -> str | None:
    """Постоянный адрес: у ссылки `${VAR:?…}` — `$VAR`, один адрес — он сам без пробелов по
    краям; иное — None."""
    text = str(value)
    ref = STRICT_REF.fullmatch(text)
    return f"${ref[1]}" if ref else text.strip() if one_address(text) else None


def trusted(value: object) -> str | None:
    """Доверенный прокси, который принимает `trusted-proxy`: `$WEB_ADDRESS` или один адрес."""
    found = address(value)
    return None if found and found.startswith("$") and found != f"${WEB_ADDRESS}" else found


def cap_drop(stack: Stack) -> Iterator[Finding]:
    for name, service in stack.services.items():
        if "ALL" not in (service.get("cap_drop") or []):
            yield name, None, "нет cap_drop: [ALL]"


def no_new_privileges(stack: Stack) -> Iterator[Finding]:
    for name, service in stack.services.items():
        if NO_NEW_PRIVILEGES not in (service.get("security_opt") or []):
            yield name, None, f"нет {NO_NEW_PRIVILEGES}"


def read_only(stack: Stack) -> Iterator[Finding]:
    for name, service in stack.services.items():
        if name not in WRITABLE_ROOT and service.get("read_only") is not True:
            yield name, None, "корень на запись, а сервиса нет в исключениях"


def _web_address(stack: Stack) -> str | None:
    """Постоянный адрес web в сети front; нет web, сети или адреса — None."""
    nets = (stack.services.get("web") or {}).get("networks")
    front = nets.get("front") if isinstance(nets, dict) else None
    return address(front.get("ipv4_address")) if isinstance(front, dict) else None


def web_address(stack: Stack) -> Iterator[Finding]:
    if _web_address(stack) is None:
        yield "web", None, "нет постоянного адреса в сети front"


def trusted_is_web(stack: Stack) -> Iterator[Finding]:
    var, web = "API_TRUSTED_PROXIES", _web_address(stack)
    for name, values in stack.env.items():
        found = trusted(values.get(var))
        if web and found and found != web:  # нет адреса web — web-address; вид — trusted-proxy
            yield name, var, "доверенный прокси — не постоянный адрес web"


def _nets(service: dict[str, Any]) -> set[str]:
    """Сети сервиса; без поля — сеть default, которую compose даёт сам: она обычная."""
    return set(service.get("networks") or ["default"])


def segments(stack: Stack) -> Iterator[Finding]:
    for name, service in stack.services.items():
        if name == "web" and "data" in _nets(service):
            yield name, None, "web в сети data: видит базу и очередь"
        elif name != "web" and _nets(service) - INTERNAL_NETS:
            yield name, None, "обычная сеть: выход наружу — только у web"


def internal(stack: Stack) -> Iterator[Finding]:
    for name, service in stack.services.items():
        for net in sorted(_nets(service) & INTERNAL_NETS):
            if (stack.networks.get(net) or {}).get("internal") is not True:
                yield name, None, f"сеть {net} без internal"
