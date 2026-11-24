"""Живой сервер (A55): `python -m ai_geo_audit.api` подпроцессом на настоящем сокете — тот же код,
что у теста, окружение задано целиком; ответы, вывод и остановка по `SIGTERM`.

Предел теста — 50 с при сумме пределов шагов 44 с (15 + 2 + 2 + 25), подпроцесс убивается в
`finally`. Порт занят между выбором и стартом — один повтор с новым портом.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import signal
import sys
import time
from pathlib import Path

import api_probes as probes
import asyncpg
import dbtools
import httpx
import live_server

import ai_geo_audit

PACKAGE = Path(ai_geo_audit.__file__).resolve()
SRC = PACKAGE.parents[1]
MARK = "query-mark-words-in-url"
SITE_WORDS = "live-site-pass-words"
ENCODED = base64.b64encode(f"user:{SITE_WORDS}".encode()).decode()


async def ready(port: int, process: asyncio.subprocess.Process) -> int:
    """`GET /api/health` 200 не дольше 15 с; число полученных ответов — у каждого своя строка."""
    answered = 0
    deadline = time.monotonic() + 15
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as http:
        while time.monotonic() < deadline and process.returncode is None:
            try:
                answered += 1
                if (await http.get("/api/health")).status_code == 200:
                    return answered
            except httpx.TransportError:
                answered -= 1
            await asyncio.sleep(0.1)
    return 0


async def headers_only(port: int, head: bytes) -> tuple[str, dict[str, list[str]], float]:
    """Заголовки без тела по сырому сокету: строка ответа, заголовки и время до них — до 2 с."""
    began = time.monotonic()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(head)
    await writer.drain()
    raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
    writer.close()
    lines = raw.decode("latin-1").split("\r\n")
    found: dict[str, list[str]] = {}
    for line in lines[1:]:
        if ":" in line:
            name, _, value = line.partition(":")
            found.setdefault(name.strip().lower(), []).append(value.strip())
    return lines[0], found, time.monotonic() - began


def headers_of(response: httpx.Response) -> dict[str, list[str]]:
    return {name: response.headers.get_list(name) for name in {k.lower() for k in response.headers}}


def headers_wrong(found: dict[str, list[str]]) -> list[str]:
    """Нет `server`, по одной копии номера, `no-store` и `nosniff`."""
    problems = ["server"] if "server" in found else []
    if len(found.get("x-request-id", [])) != 1:
        problems.append("x-request-id")
    if found.get("cache-control") != ["no-store"]:
        problems.append("cache-control")
    if found.get("x-content-type-options") != ["nosniff"]:
        problems.append("x-content-type-options")
    return problems


async def stop(process: asyncio.subprocess.Process) -> None:
    """Убийство в `finally`: живой сервер не переживает тест."""
    if process.returncode is None:
        process.kill()
        await process.wait()


async def connections_left() -> list[str]:
    server = await asyncpg.connect(dbtools._raw(dbtools.server_url()))
    try:
        rows = await server.fetch(
            "SELECT application_name FROM pg_stat_activity WHERE datname = $1", dbtools.own_name()
        )
    finally:
        await server.close()
    return [str(row["application_name"]) for row in rows]


async def test_a55_live_server_answers_logs_and_stops_on_sigterm(tmp_path: Path) -> None:
    """A55: подпроцесс с окружением из строки примера; до старта тот же интерпретатор печатает
    путь пакета — он равен пути в процессе теста. Ответы по настоящему сокету: 200, 403
    `signed-out`, на заголовки без тела — 403 и 413 `too-large` не позже 2 с (проба 4), 200 с
    `Basic` и пропуском; у каждого нет `server`, по одной копии номера, `no-store`, `nosniff`.
    `SIGTERM` — конец сигналом не позже 25 с (проба 7); каждая строка вывода — наш JSON с
    `+0000`, без `Traceback`, `color_message` и `\\x1b`; строк доступа — по числу ответов, без
    строки запроса; ни метки из адреса, ни пароля, ни строки после `Basic`, ни пропуска; после
    выхода соединений с базой копии нет."""
    polls = 0
    async with asyncio.timeout(50):
        for _attempt in range(2):
            port = live_server.free_port()
            env = live_server.live_env(port, dbtools.own_url(), SRC)
            where = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import ai_geo_audit; print(ai_geo_audit.__file__)",
                env=env,
                stdout=asyncio.subprocess.PIPE,
            )
            printed = (await where.communicate())[0].decode().strip()
            log = tmp_path / f"live-{port}.log"
            with log.open("wb") as out:
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "ai_geo_audit.api", env=env, stdout=out, stderr=out
                )
            try:
                polls = await ready(port, process)
                if polls:
                    break
            finally:
                if not polls:
                    await stop(process)
        try:
            assert polls, log.read_text("utf-8")[-1500:]
            base = f"http://127.0.0.1:{port}"
            async with httpx.AsyncClient(base_url=base) as http:
                marked = await http.get("/api/health", params={"token": MARK})
                gated = await http.get("/api/nope")
                sent = {"Authorization": f"Basic {ENCODED}", "X-Access-Token": probes.PASS}
                secret = await http.get("/api/health", headers=sent)
            nope = await headers_only(
                port, b"POST /api/nope HTTP/1.1\r\nHost: t\r\nContent-Length: 5000000\r\n\r\n"
            )
            big = await headers_only(
                port, b"POST /api/health HTTP/1.1\r\nHost: t\r\nContent-Length: 1048577\r\n\r\n"
            )
            process.send_signal(signal.SIGTERM)
            code = await asyncio.wait_for(process.wait(), 25)
        finally:
            await stop(process)
    assert printed == str(PACKAGE)
    assert (marked.status_code, marked.json()) == (200, {"status": "ok"})
    assert (gated.status_code, gated.json()) == (403, probes.sample("signed-out")["body"])
    assert secret.status_code == 200
    assert (nope[0].split(" ")[1], big[0].split(" ")[1]) == ("403", "413")
    assert (nope[2] < 2, big[2] < 2) == (True, True)
    answers = {
        "200": headers_of(marked),
        "403": headers_of(gated),
        "200 с секретами": headers_of(secret),
        "403 до тела": nope[1],
        "413 до тела": big[1],
    }
    assert {k: headers_wrong(v) for k, v in answers.items() if headers_wrong(v)} == {}
    assert code == -signal.SIGTERM
    text = log.read_text("utf-8")
    records = [json.loads(line) for line in text.splitlines()]
    assert [r for r in records if not str(r["ts"]).endswith("+0000") or "level" not in r] == []
    leaks = ["Traceback", "\x1b", "color_message", MARK, SITE_WORDS, ENCODED, probes.PASS]
    assert [part for part in leaks if part in text] == []
    uvicorn = [r["message"] for r in records if r["logger"] == "uvicorn.error"]
    assert (
        "Application startup complete." in uvicorn and "Application shutdown complete." in uvicorn
    )
    access = [r for r in records if r["logger"] == "ai_geo_audit.api.access"]
    assert len(access) == polls + 5
    assert [r["path"] for r in access if "?" in str(r["path"])] == []
    assert await connections_left() == []


#: Секрет настроек процесса — через `JWT_SECRET`: `setup_logging` отмечает его секретом. Слова.
GUARD_WORDS = "process-guard-words-for-tests-123"
#: Вход процесса и то, что в нём случается после настройки журналов.
GUARDED = {
    "warning": (
        "from ai_geo_audit.logs.setup import guard_process, setup_logging\n"
        "setup_logging()\nguard_process()\nimport os, warnings\n"
        "warnings.warn('предупреждение ' + os.environ['JWT_SECRET'])\n"
    ),
    "thread": (
        "from ai_geo_audit.logs.setup import guard_process, setup_logging\n"
        "setup_logging()\nguard_process()\nimport os, threading\n"
        "def boom():\n    raise RuntimeError('поток ' + os.environ['JWT_SECRET'])\n"
        "worker = threading.Thread(target=boom)\nworker.start()\nworker.join()\n"
    ),
    "unraisable": (
        "from ai_geo_audit.logs.setup import guard_process, setup_logging\n"
        "setup_logging()\nguard_process()\nimport os\n"
        "class Leaky:\n    def __del__(self):\n"
        "        raise RuntimeError('в деструкторе ' + os.environ['JWT_SECRET'])\n"
        "leaky = Leaky()\ndel leaky\n"
    ),
    "startup": (
        "import os\nimport ai_geo_audit.api.__main__ as entry\n"
        "def broken(process):\n    raise RuntimeError('проверка ' + os.environ['JWT_SECRET'])\n"
        "entry.check_startup = broken\nentry.main()\n"
    ),
    "entry": (
        "import os, uvicorn\n"
        "def boom(**_settings):\n    raise RuntimeError('упал ' + os.environ['JWT_SECRET'])\n"
        "uvicorn.run = boom\nfrom ai_geo_audit.api.__main__ import main\nmain()\n"
    ),
}
#: Логгер, уровень записи и код выхода процесса.
GUARDED_EXPECTED = {
    "warning": ("py.warnings", "WARNING", 0),
    "thread": ("ai_geo_audit.process", "ERROR", 0),
    "unraisable": ("ai_geo_audit.process", "ERROR", 0),
    "startup": ("ai_geo_audit.process", "ERROR", 1),
    "entry": ("ai_geo_audit.process", "ERROR", 1),
}


async def test_a55_process_warning_and_crashes_are_records_of_ours() -> None:
    """A55, строже подписанного (приёмка проверяющего 2026-11-20): после входа процесса
    предупреждение Python, необработанное исключение в потоке `threading`, исключение, которое
    интерпретатор «игнорирует» (деструктор), и необработанное в главном потоке
    `python -m ai_geo_audit.api` — и неожиданное у проверки старта — выходят записью нашего JSON
    через вычистку: каждая строка вывода — JSON, секрета настроек нет; необработанное в главном
    потоке — код выхода не ноль."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(SRC),
        "ENV_FILE": os.devnull,
        "LOG_FORMAT": "json",
        "JWT_SECRET": GUARD_WORDS,
    } | {k: v for k, v in os.environ.items() if k.startswith("COVERAGE_")}
    found: dict[str, tuple[int, int | None, bool, bool]] = {}
    for case, script in GUARDED.items():
        run = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(run.communicate(), 30)
        text = (out + err).decode()
        lines = [line for line in text.splitlines() if line]
        assert [line for line in lines if not line.startswith("{")] == [], (case, text[-1500:])
        records = [json.loads(line) for line in lines]
        logger, level, _code = GUARDED_EXPECTED[case]
        mine = [r for r in records if (r["logger"], r["level"]) == (logger, level)]
        found[case] = (len(mine), run.returncode, GUARD_WORDS in text, "***" in text)
    assert found == {
        case: (1, code, False, True) for case, (_, _, code) in GUARDED_EXPECTED.items()
    }
