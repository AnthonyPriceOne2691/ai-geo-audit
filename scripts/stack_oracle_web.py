"""Шаги оракула стека Ч1.5б: образ web (A77) и сервис web на поднятом compose (A78–A80, A82).
Импорт в одну сторону: `stack_oracle.py` ставит шаги образа до подъёма стека, шаги сервиса — до
A67.
"""

from __future__ import annotations

import re
import shutil
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from typing import TYPE_CHECKING

from stack_oracle_compose import (
    StepError,
    compose,
    health,
    inspect,
    slot,
    tag,
    top_uids,
    wait_health,
)

if TYPE_CHECKING:
    from stack_oracle import Run, Scan

DOCKERFILE = "deploy/web/Dockerfile"
#: Белый список web (A77): открыты два пути, внутри них снова закрыты пакеты, сборка, окружение.
OPEN_PATHS = ("frontend/", "deploy/web/")
CLOSED = re.compile(r"(^|/)(node_modules|dist|coverage)/|(^|/)\.env[^/]*$")
#: Канарейки web сверх общих (`plant`): закрытые места внутри открытого `frontend/`.
CANARIES = ("frontend/.env", "frontend/node_modules/canary-{}.txt")
SITE, CONFD, CONF = "usr/share/nginx/html/", "etc/nginx/conf.d/", "web.conf"
#: Чего в образе web нет нигде (A77): пакетов, исходников фронта, карт исходников.
LEFTOVERS = re.compile(r"(^|/)(node_modules|src)/|\.map$")
BUILD = ("npm ci", "npm run build", "npm run check:dist")
WEB_UID = "101"
#: Заголовки защиты у каждого ответа web (A79): значения — строкой, как в сниппете.
SECURITY = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Resource-Policy": "same-origin",
}
#: CSP страницы (A80): ровно десять директив спеки.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)
#: Кэш (A78): страница — только со сверкой, файлы сборки — год и immutable; любой путь экрана —
#: страница.
PAGE_CACHE, ASSET_CACHE, PAGES = "no-cache", "public, max-age=31536000, immutable", ("/", "/x/y")


def context_problems(names: set[str]) -> list[str]:
    """A77: в контексте web — оба открытых пути и ни одного файла вне них или в закрытом."""
    missing = [path for path in OPEN_PATHS if not any(n.startswith(path) for n in names)]
    extra = sorted(n for n in names if not n.startswith(OPEN_PATHS) or CLOSED.search(n))
    problems = [f"нет открытого пути {path}" for path in missing]
    return problems + [f"в контексте лишнее: {extra[:5]}"] * bool(extra)


def image_problems(scan: Scan, dist: set[str]) -> list[str]:
    """A77: корень сайта — ровно файлы `dist/`, в `conf.d` — только наш файл; нигде нет пакетов,
    исходников, карт, `.env*` и метки."""
    site = {n.removeprefix(SITE) for n in scan.names if n.startswith(SITE)}
    confd = sorted(n.removeprefix(CONFD) for n in scan.names if n.startswith(CONFD))
    left = sorted(n for n in scan.names if LEFTOVERS.search(n)) + scan.hits + scan.envs
    problems = [f"корень сайта: лишние {sorted(site - dist)}, нет {sorted(dist - site)}"] * (
        site != dist or not dist
    )
    problems += [f"в conf.d — {confd}, а не только {CONF}"] * (confd != [CONF])
    return problems + [f"в образе лишнее: {left[:5]}"] * bool(left)


def listening(text: str) -> set[int]:
    """A82: порты в состоянии LISTEN (`st` — 0A) из `/proc/net/tcp*` контейнера."""
    rows = (line.split() for line in text.splitlines())
    return {int(row[1].rsplit(":", 1)[1], 16) for row in rows if row[3:4] == ["0A"]}


def page(url: str) -> int | str:
    """Код ответа страницы с машины, мимо прокси окружения; сбой — его имя."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=5) as response:
            return int(response.status)
    except OSError as error:
        return f"{type(error).__name__}: {error}"


def fetch(url: str) -> tuple[int, Message, bytes]:
    """Ответ web с машины мимо прокси окружения: код, все значения заголовков, тело. Ответ 4xx и
    5xx — тоже ответ; сбой соединения — красное шага с именем сбоя (A78–A80)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=5) as response:
            return int(response.status), response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()
    except OSError as error:
        raise StepError(f"{url}: {type(error).__name__}: {error}") from None


def header_problems(where: str, headers: Message, wanted: dict[str, str]) -> list[str]:
    """Каждый заголовок `wanted` — ровно один и со своим значением; иначе — имя и что пришло."""
    found = {name: headers.get_all(name) or [] for name in wanted}
    return [f"{where}: {name} — {got}" for name, got in found.items() if got != [wanted[name]]]


def csp_rules(value: str) -> list[tuple[str, list[str]]]:
    """Директивы CSP по порядку: имя и источники без учёта их порядка."""
    return [
        (rule[0].lower(), sorted(rule[1:])) for rule in map(str.split, value.split(";")) if rule
    ]


def csp_problems(found: list[str], wanted: str = CSP) -> list[str]:
    """A80: CSP — один заголовок, директивы — ровно директивы `wanted`: лишняя, пропавшая,
    повторённая или с другими источниками — красное с её именем. Из повторённых действует первая,
    как в браузере."""
    if len(found) != 1:
        return [f"заголовков CSP {len(found)}, а не один"]
    rules, spec = csp_rules(found[0]), dict(csp_rules(wanted))
    names, got = [name for name, _ in rules], dict(reversed(rules))
    problems = [
        f"директива {name} повторена" for name in sorted(set(names)) if names.count(name) > 1
    ]
    problems += [f"лишняя директива {name}" for name in sorted(got.keys() - spec.keys())]
    problems += [f"нет директивы {name}" for name in sorted(spec.keys() - got.keys())]
    return problems + [
        f"{name} — {' '.join(got[name])}, а не {' '.join(sources)}"
        for name, sources in spec.items()
        if name in got and got[name] != sources
    ]


def web_url(run: Run, path: str) -> str:
    """Адрес web с машины: петля и порт прогона (A82)."""
    return f"http://127.0.0.1:{slot(run)[0]}{path}"


def dist_page(run: Run) -> str:
    """A78: `index.html` из `dist/` стадии сборки — с ним сверяется страница web."""
    container = f"{run.name}-web-dist"
    run.docker.containers.append(container)
    flags = ["--rm", "--name", container, "--network", "none", "--entrypoint", "cat"]
    return run.docker("run", *flags, tag(run, "web-build"), "dist/index.html")


def asset(run: Run) -> str:
    """Путь файла сборки для проверок — первый по имени в `assets/` стадии сборки."""
    found = sorted(name for name in run.facts["dist"] if name.startswith("assets/"))
    if not found:
        raise StepError("в dist/ стадии сборки нет файлов assets/ — судить кэш сборки не по чему")
    return f"/{found[0]}"


def step_web_build(run: Run) -> str:
    """A77: канарейки web с меткой прогона; цели `web` и `build` из чистой копии; файлы `dist/`
    стадии сборки — для сверки с корнем сайта."""
    for name in CANARIES:
        canary = run.copy / name.format(run.label)
        canary.parent.mkdir(parents=True, exist_ok=True)
        canary.write_text(f"{run.label}\n", encoding="utf-8")
    for target, built in (("web", tag(run, "web")), ("build", tag(run, "web-build"))):
        run.docker.images.append(built)
        build = ["build", "--file", str(run.copy / DOCKERFILE), "--target", target]
        run.docker(*build, "--tag", built, str(run.copy), limit="build")
    container = f"{run.name}-web-dist"
    run.docker.containers.append(container)
    flags = ["--rm", "--name", container, "--network", "none", "--entrypoint", "find"]
    found = run.docker("run", *flags, tag(run, "web-build"), "dist", "-type", "f").split()
    run.facts["dist"] = {name.removeprefix("dist/") for name in found}
    return f"образ {tag(run, 'web')}; в dist/ стадии сборки файлов {len(run.facts['dist'])}"


def step_web_files(run: Run) -> str:
    """A77: в стадии сборки прошли `npm ci`, `npm run build`, `npm run check:dist`; в контексте —
    только открытые пути; в образе — ровно `dist/` в корне сайта и наш файл в `conf.d`."""
    show = ["history", "--no-trunc", "--format", "{{.CreatedBy}}", tag(run, "web-build")]
    history = run.docker(*show, limit="docker")
    problems = [f"в стадии сборки нет {step}" for step in BUILD if step not in history]
    probe = run.copy.parent / "ctx-web"
    probe.mkdir()
    (probe / "ctx.Dockerfile").write_text(f"FROM {tag(run, 'web')}\nCOPY . /ctx/\n", "utf-8")
    shutil.copy(run.copy / f"{DOCKERFILE}.dockerignore", probe / "ctx.Dockerfile.dockerignore")
    context = tag(run, "web-context")
    run.docker.images.append(context)
    build = ["build", "--file", str(probe / "ctx.Dockerfile"), "--tag", context, str(run.copy)]
    run.docker(*build, limit="build")
    problems += context_problems(run.export(context, "ctx/").names)
    problems += image_problems(run.export(tag(run, "web")), run.facts["dist"])
    if problems:
        raise StepError("; ".join(problems))
    count = len(run.facts["dist"])
    return f"{', '.join(BUILD)} прошли; корень сайта — ровно {count} файлов dist/, conf.d — {CONF}"


def step_web_process(run: Run) -> str:
    """A82: процессы web — uid 101, root нет; nginx слушает порты выше 1024; корень только для
    чтения; порт опубликован только на петле, и страница по нему с машины — 200."""
    web = inspect(run, "web")
    uids = top_uids(run.docker("top", web["Id"], "-eo", "uid,pid,args", limit="docker"))
    tcp = compose(run, "exec", "-T", "web", "sh", "-c", "cat /proc/net/tcp*", limit="docker")
    ports = listening(tcp)
    bound = [b for found in (web["NetworkSettings"]["Ports"] or {}).values() for b in found or []]
    hosts = sorted({b["HostIp"] for b in bound})
    code = page(f"http://127.0.0.1:{bound[0]['HostPort']}/") if bound else "не опубликован"
    problems = [f"uid процессов {sorted(uids)}"] * (uids != {WEB_UID})
    problems += [f"слушает порты {sorted(ports)}"] * (not ports or min(ports) <= 1024)
    problems += ["корень не только для чтения"] * (web["HostConfig"]["ReadonlyRootfs"] is not True)
    problems += [f"порт на {hosts}, страница — {code}"] * (hosts != ["127.0.0.1"] or code != 200)
    if problems:
        raise StepError("; ".join(problems))
    port = bound[0]["HostPort"]
    return f"uid {WEB_UID}; слушает {sorted(ports)}; корень — только чтение; 127.0.0.1:{port} — 200"


def step_web_without_api(run: Run) -> str:
    """A82, вторая строка: api остановлен на 40 с — web всё это время healthy: его проверка
    смотрит страницу, а не `/api/health`. Затем api снова healthy."""
    compose(run, "stop", "api", limit="up")
    seen: set[str] = set()
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        seen.add(str(health(run, "web")))
        time.sleep(2)
    compose(run, "start", "api", limit="up")
    wait_health(run, "api", "healthy", 90)
    if seen != {"healthy"}:
        raise StepError(f"web за 40 с без api — {sorted(seen)}")
    return "web healthy все 40 с без api; api снова healthy"


def step_web_cache(run: Run) -> str:
    """A78: `/` и `/x/y` — 200 и тот же `index.html`, что в `dist/` стадии сборки, с
    `Cache-Control: no-cache`; файл из `/assets/` — 200 и ровно один `Cache-Control` на год с
    `immutable`."""
    html, file = dist_page(run), asset(run)
    problems = []
    for path in (*PAGES, file):
        code, headers, body = fetch(web_url(run, path))
        cache = ASSET_CACHE if path == file else PAGE_CACHE
        problems += [f"{path}: код {code}"] * (code != 200)
        same = path == file or body.decode("utf-8", "replace") == html
        problems += [f"{path}: не index.html из dist/"] * (not same)
        problems += header_problems(path, headers, {"Cache-Control": cache})
    if problems:
        raise StepError("; ".join(problems))
    return f"{', '.join(PAGES)} — 200, index.html из dist/, {PAGE_CACHE}; {file} — {ASSET_CACHE}"


def step_web_headers(run: Run) -> str:
    """A79, первая строка: у страницы и файла сборки — ровно по одному каждому из пяти заголовков
    защиты; `Server` — `nginx` без версии. Ответы `/api/` — срез 3."""
    problems = []
    for path in (*PAGES, asset(run)):
        code, headers, _ = fetch(web_url(run, path))
        problems += [f"{path}: код {code}"] * (code != 200)
        problems += header_problems(path, headers, SECURITY | {"Server": "nginx"})
    if problems:
        raise StepError("; ".join(problems))
    return f"страница и файл сборки — по одному: {', '.join(SECURITY)}; Server — nginx"


def step_web_csp(run: Run) -> str:
    """A80: CSP страницы — ровно десять директив спеки; у страницы
    `Cross-Origin-Opener-Policy: same-origin`."""
    problems = []
    for path in PAGES:
        _, headers, _ = fetch(web_url(run, path))
        found = headers.get_all("Content-Security-Policy") or []
        problems += [f"{path}: {problem}" for problem in csp_problems(found)]
        problems += header_problems(path, headers, {"Cross-Origin-Opener-Policy": "same-origin"})
    if problems:
        raise StepError("; ".join(problems))
    return f"{', '.join(PAGES)}: CSP — ровно десять директив спеки; COOP — same-origin"


#: Шаги образа web — до подъёма стека; сборка нужна подъёму.
WEB_BUILD_STEPS: list[tuple[str, str, Callable[[Run], str], bool]] = [
    ("A77", "сборка образа web с канарейками", step_web_build, True),
    ("A77", "стадия сборки, контекст и файлы образа web", step_web_files, False),
]
#: Шаги сервиса web — на живом стеке, до шага A67.
WEB_STACK_STEPS: list[tuple[str, str, Callable[[Run], str], bool]] = [
    ("A82", "процессы, порт и корень web", step_web_process, False),
    ("A78", "страница и кэш web", step_web_cache, False),
    ("A79", "заголовки защиты web", step_web_headers, False),
    ("A80", "CSP страницы web", step_web_csp, False),
    ("A82", "web без api 40 с", step_web_without_api, False),
]
