"""Redis тестов (Ч1.14а1, срез 1): стенд по образу стека, у процесса pytest своя база из 1–15
меткой со сроком (L97), отказ — одним сообщением, как у Postgres (A12)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time

import dbtools
import pytest
import redistools
import yaml

ROOT = dbtools.ROOT
#: Порт, где Redis нет: отказ соединения сразу. Пароль адресу не нужен (A122).
DEAD = "redis://127.0.0.1:1"
#: Короткая сессия pytest подпроцессом: после занятия базы печатает её номер и владельца метки.
SPY = """
import sys
import pytest
class Spy:
    def pytest_collection_finish(self, session):
        import redistools
        print("redis-session", redistools.session_db(), redistools.owner(), flush=True)
test = "tests/test_config.py::test_a13_dev_database_is_the_compose_one_on_5462"
sys.exit(pytest.main(["-p", "no:cacheprovider", test], plugins=[Spy()]))
"""
#: Короткая сессия подпроцессом: берёт клиента оснастки, а после её конца печатает, сколько
#: клиентов осталось незакрытыми.
CLOSED = """
import sys
import pytest
seen = []
class Spy:
    def pytest_collection_finish(self, session):
        import redistools
        redistools.client(redistools.session_db()).ping()
        seen.append(redistools)
test = "tests/test_config.py::test_a13_dev_database_is_the_compose_one_on_5462"
code = pytest.main(["-p", "no:cacheprovider", test], plugins=[Spy()])
print("helper-clients-left", len(seen[0]._CLIENTS), flush=True)
sys.exit(code)
"""


def services(name: str) -> dict[str, dict[str, object]]:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))["services"]


def gone(key: str, within: float = 2.0) -> bool:
    """Метки `key` нет — ждать её срока не дольше `within`, а не «сном на глаз»."""
    deadline = time.monotonic() + within
    while redistools.client().exists(key):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def test_dev_redis_is_the_stack_image_with_the_stand_password() -> None:
    """Стенд: сервис `redis` — строка образа стека по дайджесту (A100), петля 6462, пароль
    стенда — тот же, что в адресе тестов; готовность — `redis-cli -e` с паролем: без `-e`
    ответ-ошибка даёт код 0 (L60)."""
    dev, stack = (
        services("docker-compose.dev.yml")["redis"],
        services("docker-compose.yml")["redis"],
    )
    assert dev["image"] == stack["image"]
    assert "@sha256:" in str(dev["image"])
    assert dev["ports"] == ["127.0.0.1:6462:6379"]
    assert dev["command"] == ["redis-server", "--requirepass", redistools.PASSWORD]
    assert dev["environment"] == {"REDISCLI_AUTH": redistools.PASSWORD}
    assert dev["healthcheck"]["test"] == ["CMD", "redis-cli", "-h", "127.0.0.1", "-e", "ping"]
    assert dev["tmpfs"] == ["/data"]
    assert f":{redistools.PASSWORD}@127.0.0.1:6462" in redistools.SERVER


def test_session_holds_its_own_database_under_a_label() -> None:
    """База сессии — одна из 1–15; метка в базе 0 — корень копии и pid, со сроком не больше
    `TTL_MS`."""
    db = redistools.session_db()
    key = redistools.label(db)
    assert db in redistools.DATABASES
    assert redistools.client().get(key) == redistools.owner().encode()
    assert 0 < redistools.client().pttl(key) <= redistools.TTL_MS
    assert redistools.owner() == f"{ROOT}|{os.getpid()}"


def test_a_second_claim_takes_another_database_and_release_frees_it() -> None:
    """Занятую базу заявка пропускает; отпущенная свободна; чужую метку отпуск не снимает."""
    other = redistools.claim("probe-owner", ttl_ms=10_000)
    try:
        assert other != redistools.session_db()
        assert redistools.release(other, "someone-else") is False
        assert redistools.client().get(redistools.label(other)) == b"probe-owner"
    finally:
        assert redistools.release(other, "probe-owner") is True
    assert not redistools.client().exists(redistools.label(other))


def test_a_killed_session_frees_its_database_by_the_label_term(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L97: сессия, снятая по сроку (порча mutmut), метку не снимает — базу отпускает срок метки."""
    killed = redistools.claim("killed-session", ttl_ms=300)
    monkeypatch.setattr(redistools, "DATABASES", range(killed, killed + 1))
    try:
        with pytest.raises(redistools.NoFreeDatabaseError):
            redistools.claim("next-session")
        assert gone(redistools.label(killed))
        assert redistools.claim("next-session", ttl_ms=10_000) == killed
    finally:
        redistools.release(killed, "next-session")


def test_a_live_session_keeps_its_label_by_renewal() -> None:
    """Живая сессия продлевает метку чаще срока; после остановки продления метка уходит сроком."""
    db = redistools.claim("long-session", ttl_ms=400)
    holder = redistools.Holder(db, "long-session", ttl_ms=400, every=0.1)
    holder.start()
    try:
        time.sleep(0.9)
        assert redistools.client().get(redistools.label(db)) == b"long-session"
    finally:
        holder.stop()
    assert gone(redistools.label(db))


def test_no_free_database_is_one_reason_with_the_holders(monkeypatch: pytest.MonkeyPatch) -> None:
    """Свободной базы нет — одна причина словами: какие базы, чьи метки, когда отпустит срок;
    пароля в тексте нет."""
    own = redistools.session_db()
    monkeypatch.setattr(redistools, "DATABASES", range(own, own + 1))
    with pytest.raises(redistools.NoFreeDatabaseError) as caught:
        redistools.claim("late-session")
    text = str(caught.value)
    assert f"заняты базы {own}–{own}" in text
    assert redistools.owner() in text
    assert f"срок метки — {redistools.TTL_MS // 1000} с" in text
    assert redistools.PASSWORD not in text


def test_redis_url_fixture_gives_the_sessions_database_empty(redis_url: str) -> None:
    """Адрес Redis теста — база сессии на сервере `TEST_REDIS_URL` или стенда, пустая к началу
    теста; `flush` чистит только её. Сообщение проверки адреса не печатает: в нём пароль (аргументы
    упавшего теста pytest печатает сам — пароль стенда публичный)."""
    db = redistools.session_db()
    same = redis_url == redistools.url(db)
    assert same, f"адрес фикстуры — не база сессии {db}"
    assert redistools.client(db).dbsize() == 0
    redistools.client(db).set("ai-geo-audit:alerts:out", "x")
    redistools.flush(db)
    assert redistools.client(db).dbsize() == 0
    assert redistools.client().exists(redistools.label(db))


def test_a_finished_session_releases_its_label() -> None:
    """Конец сессии снимает её метку: под mutmut каждая порча — своя сессия pytest, и без снятия 15
    баз кончились бы за срок метки (находка 2 второй проверяющей, срез 1)."""
    env = {k: v for k, v in os.environ.items() if k != "MUTANT_UNDER_TEST"}
    run = subprocess.run(
        [sys.executable, "-c", SPY],
        cwd=dbtools.BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    found = re.search(r"^redis-session (\d+) (.+)$", run.stdout, re.MULTILINE)
    assert run.returncode == 0 and found, run.stdout[-1500:] + run.stderr[-1500:]
    db, who = int(found.group(1)), found.group(2)
    assert redistools.client().get(redistools.label(db)) != who.encode()


def test_helper_clients_are_one_per_address_and_closed_at_session_end() -> None:
    """Клиент оснастки — один на адрес за сессию, конец сессии его закрывает: сокет не уходит
    сборщику мусора. В CI 45626254595 незакрытый сокет к Redis всплыл в `pytest_unconfigure` и
    уронил долю порч (срез 3): клиент создавался на каждый вызов и не закрывался."""
    db = redistools.session_db()
    assert redistools.client(db) is redistools.client(db)
    env = {k: v for k, v in os.environ.items() if k != "MUTANT_UNDER_TEST"}
    run = subprocess.run(
        [sys.executable, "-c", CLOSED],
        cwd=dbtools.BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run.returncode == 0, run.stdout[-1500:] + run.stderr[-1500:]
    assert "helper-clients-left 0" in run.stdout.splitlines(), run.stdout[-1500:]


def test_without_redis_the_run_stops_once() -> None:
    """Как A12 у Postgres: Redis не отвечает — одно сообщение с командой подъёма, без пропусков
    и трассировки."""
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_config.py"],
        cwd=dbtools.BACKEND,
        env={**os.environ, "TEST_REDIS_URL": DEAD},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    out = run.stdout + run.stderr
    assert run.returncode == pytest.ExitCode.USAGE_ERROR, out
    assert out.count(redistools.START) == 1
    assert "Redis для тестов не отвечает (ConnectionError" in out
    assert "skipped" not in out and "passed" not in out and "Traceback" not in out
