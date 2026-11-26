"""Шаги redis оракула без Docker (A171, две строки): ответы `redis-cli` разовому контейнеру сети
`data`, здоровье и записи проверки, команда контейнера, главный процесс, процессы и мягкая
остановка; итог шага — строкой целиком, у каждой ветки — свой тест (L88). Пароль — только в
окружении команды, в её строке — имя без значения (L84). Модули `scripts/` лежат вне пакета."""

from __future__ import annotations

import importlib
import subprocess
import sys
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import dbtools
import pytest

sys.path.insert(0, str(dbtools.ROOT / "scripts"))
redis = importlib.import_module("stack_oracle_redis")
compose_mod = importlib.import_module("stack_oracle_compose")
QUEUE = "queue-value-123456"
CLI_ARGS = ("run", "--rm", "--no-deps", "-e", "REDISCLI_AUTH", "redis", "redis-cli", "-h", "redis")
Done = subprocess.CompletedProcess[bytes]


def done(stdout: str = "", stderr: str = "", code: int = 0) -> Done:
    return subprocess.CompletedProcess(["docker"], code, stdout.encode(), stderr.encode())


NOAUTH = done("NOAUTH Authentication required.\n")
WRONGPASS = done(
    "NOAUTH Authentication required.\n",
    "AUTH failed: WRONGPASS invalid username-password pair or user is disabled.\n",
)
PONG = done("PONG\n")
#: Redis не ответил: `redis-cli` пишет отказ соединения в stderr, в stdout пусто.
REFUSED = done("", "Could not connect to Redis at redis:6379\n", 1)


class Cli:
    """Подставной исполнитель: ответ `redis-cli` — по паролю в окружении команды (нет, верный,
    неверный); журнал — аргументы и окружение каждого вызова."""

    def __init__(self, none: Done = NOAUTH, wrong: Done = WRONGPASS, right: Done = PONG) -> None:
        self.answers = {"none": none, "wrong": wrong, "right": right}
        self.calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def result(self, *args: str, env: dict[str, str], **kw: Any) -> Done:
        self.calls.append((args, env))
        auth = env.get("REDISCLI_AUTH")
        return self.answers["none" if auth is None else "right" if auth == QUEUE else "wrong"]


@pytest.fixture
def cli_run(monkeypatch: pytest.MonkeyPatch) -> Callable[..., SimpleNamespace]:
    """Прогон с подставным `redis-cli`; в окружении вызывающего — посторонний `REDISCLI_AUTH`."""
    monkeypatch.setattr(redis, "compose_base", lambda run: ["compose", "-p", "stack"])
    monkeypatch.setattr(
        redis, "stack_env", lambda run: {"REDISCLI_AUTH": "leak", "REDIS_PASSWORD": QUEUE}
    )
    return lambda **answers: SimpleNamespace(docker=Cli(**answers), redis_password=QUEUE)


def test_a171_auth_step_without_wrong_and_right_password(cli_run: Callable) -> None:
    """A171, первая строка: без пароля — `NOAUTH`, с неверным — `WRONGPASS` в stderr и `PONG` нет,
    с верным — `PONG`: итог шага целиком. Пароль — только в окружении: без пароля переменной нет
    вовсе, хоть у вызывающего она и была; в строке команды — имя без значения."""
    run = cli_run()
    line = redis.step_auth(run)
    assert line == "без пароля — NOAUTH; неверный — WRONGPASS в stderr, PONG нет; верный — PONG"
    (none_args, none_env), (_, wrong_env), (_, right_env) = run.docker.calls
    assert none_args == ("compose", "-p", "stack", *CLI_ARGS, "ping")
    assert "REDISCLI_AUTH" not in none_env and none_env["REDIS_PASSWORD"] == QUEUE
    assert wrong_env["REDISCLI_AUTH"] not in (QUEUE, "leak")
    assert right_env["REDISCLI_AUTH"] == QUEUE
    assert not [args for args, _ in run.docker.calls if any(QUEUE in word for word in args)]


@pytest.mark.parametrize(
    ("answers", "reason"),
    [
        ({"none": PONG}, "без пароля — ответ «PONG», а не NOAUTH"),
        ({"none": REFUSED}, "без пароля — ответ «», а не NOAUTH"),
        ({"none": done("NOAUTH PONG\n")}, "без пароля — ответ «NOAUTH PONG», а не NOAUTH"),
        (
            {"wrong": REFUSED},
            "неверный пароль — ответ «», stderr «Could not connect to Redis at redis:6379»: нужен"
            " WRONGPASS без PONG",
        ),
        (
            {"wrong": done("PONG\n")},
            "неверный пароль — ответ «PONG», stderr «»: нужен WRONGPASS без PONG",
        ),
        (
            {"wrong": done("PONG\n", "AUTH failed: WRONGPASS …\n")},
            "неверный пароль — ответ «PONG», stderr «AUTH failed: WRONGPASS …»: нужен WRONGPASS"
            " без PONG",
        ),
        ({"right": NOAUTH}, "верный пароль — ответ «NOAUTH Authentication required.», а не PONG"),
    ],
    ids=[
        "no-password-pong",
        "no-password-refused",
        "no-password-noauth-and-pong",
        "wrong-refused",
        "wrong-no-wrongpass",
        "wrong-but-pong",
        "right-noauth",
    ],
)
def test_a171_auth_step_is_red_on_each_wrong_answer(
    cli_run: Callable, answers: dict[str, Done], reason: str
) -> None:
    """A171, первая строка: порча «снята строка `requirepass`» — `PONG` без пароля; неверный
    пароль без `WRONGPASS` или с ответом `PONG`; верный — без `PONG`: красное с причиной целиком."""
    with pytest.raises(compose_mod.StepError) as red:
        redis.step_auth(cli_run(**answers))
    assert str(red.value) == reason


TOP_5X = (
    "SERVICE  #   UID  PID    PPID   C   STIME  TTY  TIME      CMD\n"
    "redis    1   999  62057  62034  0   13:28  ?    00:00:00  redis-server *:6379\n"
)
TOP_2X = (
    "stack-redis-1\n"
    "UID  PID  PPID  C  STIME  TTY  TIME      CMD\n"
    "999  10   1     0  13:28  ?    00:00:00  sh -c exec redis-server -\n"
    "999  11   10    0  13:28  ?    00:00:00  redis-server *:6379\n"
    "\n"
)


@pytest.mark.parametrize(
    ("text", "commands"),
    [
        (TOP_5X, ["redis-server *:6379"]),
        (TOP_2X, ["sh -c exec redis-server -", "redis-server *:6379"]),
        ("", []),
    ],
    ids=["compose-5x", "compose-2x", "empty"],
)
def test_a171_top_commands_take_the_cmd_column_whole(text: str, commands: list[str]) -> None:
    """A171, вторая строка: строки процессов `docker compose top` — столбец CMD целиком, с
    пробелами; вид 5.x — одна таблица со столбцами SERVICE и # (проба 5 среза 0), 2.x — блок."""
    assert redis.top_commands(text) == commands


def healthy(**config: Any) -> dict[str, Any]:
    """`docker inspect` запущенного redis: healthy, записи — `PONG`, команда `sh -c`."""
    script = "exec redis-server - <<EOF\nrequirepass $REDIS_PASSWORD\nEOF\n"
    base = {"Cmd": ["sh", "-c", script], "User": "redis"}
    log = [{"Output": "PONG\n"}, {"Output": "PONG\n"}]
    return {"State": {"Health": {"Status": "healthy", "Log": log}}, "Config": base | config}


class Redis:
    """Подставной стек для второй строки: `inspect` — по очереди (до и после остановки), ответы
    `exec`, `top` и `stop` — по первому слову команды; журнал — аргументы."""

    def __init__(
        self,
        states: list[dict[str, Any]] | None = None,
        inside: str = "redis-server\n999\n999\npipe:[1456750]\n",
        top: str = TOP_5X,
        stop: Done | None = None,
    ) -> None:
        # Копия: `inspect` снимает состояния по очереди, а список может быть данными `parametrize`.
        self.states = list(states or [healthy(), {"State": {"ExitCode": 0}, "Config": {}}])
        self.answers = {"exec": done(inside), "top": done(top), "stop": stop or done()}
        self.calls: list[tuple[str, ...]] = []

    def inspect(self, run: object, service: str) -> dict[str, Any]:
        assert service == "redis"
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def compose_result(self, run: object, *args: str, **kw: Any) -> Done:
        self.calls.append(args)
        return self.answers[args[0]]


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Redis]:
    def make(**kw: Any) -> Redis:
        fake = Redis(**kw)
        monkeypatch.setattr(redis, "inspect", fake.inspect)
        monkeypatch.setattr(redis, "compose_result", fake.compose_result)
        return fake

    return make


def test_a171_process_step_line_and_commands(stack: Callable[..., Redis]) -> None:
    """A171, вторая строка: верный redis — итог шага целиком; команды — `exec -T redis sh -c`,
    `top redis` и `stop redis` последней."""
    fake = stack()
    line = redis.step_process(SimpleNamespace())
    assert line == (
        "healthy, записи — PONG; пользователь redis; PID 1 — redis-server, uid 999, вход — канал;"
        " процессы — redis-server *:6379; остановка — код 0 за 0.0 с"
    )
    assert fake.calls == [
        ("exec", "-T", "redis", "sh", "-c", redis.INSIDE),
        ("top", "redis"),
        ("stop", "redis"),
    ]


EXPANDED = "exec redis-server - <<EOF\nrequirepass the-value-itself\nEOF\n"


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        (
            {"states": [{"State": {"Health": {"Status": "starting", "Log": []}}, "Config": {}}]},
            "здоровье redis — starting, записи проверки — []",
        ),
        (
            {
                "states": [
                    healthy()
                    | {
                        "State": {
                            "Health": {
                                "Status": "healthy",
                                "Log": [{"Output": "NOAUTH Authentication required.\n"}],
                            }
                        }
                    }
                ]
            },
            "здоровье redis — healthy, записи проверки — ['NOAUTH Authentication required.']",
        ),
        (
            {"states": [healthy(Cmd=["redis-server", "--requirepass", "x"])]},
            "Config.Cmd — не строка sh -c с нераскрытым $REDIS_PASSWORD",
        ),
        (
            {"states": [healthy(Cmd=["sh", "-c", EXPANDED])]},
            "Config.Cmd — не строка sh -c с нераскрытым $REDIS_PASSWORD",
        ),
        (
            {"inside": "sh\n999\n999\npipe:[1]\n"},
            "PID 1 — sh, uid 999 (redis — 999), вход — pipe:[1]",
        ),
        (
            {"inside": "redis-server\n0\n999\npipe:[1]\n"},
            "PID 1 — redis-server, uid 0 (redis — 999), вход — pipe:[1]",
        ),
        (
            {"inside": "redis-server\n1000\n999\npipe:[1]\n"},
            "PID 1 — redis-server, uid 1000 (redis — 999), вход — pipe:[1]",
        ),
        (
            {"inside": "redis-server\n999\n999\n/dev/null\n"},
            "PID 1 — redis-server, uid 999 (redis — 999), вход — /dev/null",
        ),
        ({"inside": ""}, "PID 1 — , uid  (redis — ), вход — "),
        ({"top": TOP_2X}, "процессы redis — ['sh -c exec redis-server -', 'redis-server *:6379']"),
        ({"stop": done(code=1)}, "остановка — код 1, выход 0, 0.0 с"),
        (
            {"states": [healthy(), {"State": {"ExitCode": 137}, "Config": {}}]},
            "остановка — код 0, выход 137, 0.0 с",
        ),
    ],
    ids=[
        "not-healthy",
        "health-noauth",
        "not-sh-c",
        "password-expanded",
        "pid1-sh",
        "uid-root",
        "uid-not-redis",
        "stdin-not-pipe",
        "nothing-inside",
        "sh-process",
        "stop-failed",
        "killed",
    ],
)
def test_a171_process_step_is_red_on_each_branch(
    stack: Callable[..., Redis], kw: dict[str, Any], reason: str
) -> None:
    """A171, вторая строка, порчи и ветки: не healthy и запись `NOAUTH` (проверка здоровья Ч1.5а
    без пароля); команда не `sh -c` или пароль раскрыт в ней; главный процесс `sh` (без `exec`),
    uid 0 (нет `user:`) или не пользователя образа, вход не канал; лишний процесс `sh`; остановка с
    ошибкой или убийством (код 137): красное с причиной целиком."""
    stack(**kw)
    with pytest.raises(compose_mod.StepError) as red:
        redis.step_process(SimpleNamespace())
    assert str(red.value) == reason


def test_a171_slow_stop_is_red(
    stack: Callable[..., Redis], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A171, вторая строка: остановка за 10 с и дольше — красное: докер убивает по сроку."""
    stack()
    ticks = iter([100.0, 110.0])
    monkeypatch.setattr(redis, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    with pytest.raises(compose_mod.StepError) as red:
        redis.step_process(SimpleNamespace())
    assert str(red.value) == "остановка — код 0, выход 0, 10.0 с"


def test_a171_redis_steps_come_last_before_the_final_search() -> None:
    """A171: шаги redis — два, номер A171, последними в таблице оракула перед поиском секретов
    после всех шагов (A172, первая строка): вторая строка останавливает redis."""
    oracle = importlib.import_module("stack_oracle")
    search = importlib.import_module("stack_oracle_search")
    assert [examples for examples, *_ in redis.REDIS_STEPS] == ["A171", "A171"]
    assert oracle.STEPS[-3:] == [*redis.REDIS_STEPS, *search.FINAL_SEARCH]
