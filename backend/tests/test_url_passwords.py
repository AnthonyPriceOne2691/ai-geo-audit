"""A122: адрес вида `схема://:пароль@узел` в отслеживаемых файлах — только поимённые значения.

Сканер секретов этот вид не ловит (имя пустое), поэтому это свой тест, а не новый гейт.
"""

from __future__ import annotations

import re
import subprocess

import envguard

ROOT = envguard.ROOT
URL = re.compile(r"\b([a-z][a-z0-9+.-]*)://:([^@\s/'\"`]+)@")
#: Пароль в адресе → причина. Секретов здесь нет: заглушки образцов и документов, тестовые значения.
ALLOWED = {
    "CHANGE_ME": "заглушка в `.env.example` и документах",
    "real-pass-123456": "тестовое значение пробы маскировки (test_config)",
    "{REDIS_PASSWORD}": "подстановка f-строки теста, значение — тестовая константа (test_logs)",
    "{password}": "подстановка f-строки теста (test_logs)",
    "{redis}": "подстановка f-строки фейка файла сервера, значение — константа (deploy_fakes)",
    "{PROD_QUEUE_WORDS}": "подстановка f-строки боевого режима проб api (api_probes)",
    "r3dis-pass-123456": "тестовое значение из спеки Ч1.2в в архиве поставок",
    "r3dis%40pass-123456": "тестовое значение A176 из спеки «Учёток хранилищ»",
    "<пароль>": "заглушка в документах",
    "…": "многоточие в документах",
}


def url_problems(name: str, text: str) -> list[str]:
    """Нарушения в одном файле: файл, строка, вид адреса — без самого пароля."""
    return [
        f"{name}:{number}: {scheme}://:…@ — пароль в адресе не из списка исключений"
        for number, line in enumerate(text.splitlines(), 1)
        for scheme, password in URL.findall(line)
        if password not in ALLOWED
    ]


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    return [name for name in out.stdout.decode().split("\0") if name]


def test_a122_url_passwords_only_in_named_values() -> None:
    """A122: по `git ls-files` вид `схема://:пароль@узел` — только в исключениях."""
    problems = []
    for name in tracked():
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):  # silent-ok: двоичный файл или нет на диске
            continue
        problems += url_problems(name, text)
    assert problems == []


def test_a122_every_exception_has_a_reason() -> None:
    """A122: у каждого исключения — причина."""
    assert [value for value, reason in ALLOWED.items() if not reason.strip()] == []


def test_a122_copy_password_in_redis_url_is_named() -> None:
    """A122, копия: в файле `redis://:<пароль>@<узел>` — красный: файл, строка, вид адреса."""
    text = "x = 1\nREDIS_URL=redis://:" + "s3cret-value-123@cache:6379/0\n"
    assert url_problems("probe.env", text) == [
        "probe.env:2: redis://:…@ — пароль в адресе не из списка исключений"
    ]
