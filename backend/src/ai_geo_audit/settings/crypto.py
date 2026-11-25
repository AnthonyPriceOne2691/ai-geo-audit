"""Шифр ключей сервисов (A220–A223): Fernet по строкам файла мастер-ключа, номер мастер-ключа,
отпечаток и подсказка.

Из строки файла HMAC-SHA256 выводятся три ключа, у каждого назначения своя метка: ключ Fernet,
номер мастер-ключа и ключ отпечатка (decisions, срез 0). `MultiFernet` шифрует первой строкой, а
расшифровывает любой. Шифруется пара «имя и значение» одним JSON; при расшифровке имя
сверяется: шифротекст одного ключа в строке другого — отказ, а не чужое значение (b1:444).
Отпечаток — от имени и значения на ключе текущей строки: голый хеш короткого пароля подбирается
(b1:469–470). Строки файла и значения уходят в вычистку журнала до любой записи. Отказ
поднимается вне блока `except`: в цепочке исключения нет ни шифротекста, ни значения (A237).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from ai_geo_audit import config
from ai_geo_audit.config import master_key
from ai_geo_audit.logs import register_secret
from ai_geo_audit.settings import registry

FERNET_LABEL = b"ai-geo-audit fernet v1"
MASTER_ID_LABEL = b"ai-geo-audit master-id v1"
FINGERPRINT_LABEL = b"ai-geo-audit fingerprint v1"
MASTER_ID_CHARS = 8
FINGERPRINT_CHARS = 12
#: Подсказка — последние знаки длинного ключа: у короткого она открыла бы слишком большую долю.
HINT_FROM = 20
HINT_CHARS = 4


class KeyUnreadable(Exception):  # noqa: N818 -- имя отказа из спеки (A223)
    """Шифротекст ключа не открывается: чужой ключ, испорченный шифротекст или мастер-ключ,
    которого нет в файле. Текст — со словом ключа, без значения и шифротекста."""


@dataclass(frozen=True, slots=True)
class Sealed:
    """Ключ для записи в базу: шифротекст, номер мастер-ключа, отпечаток и подсказка."""

    ciphertext: str = field(repr=False)
    master_id: str
    fingerprint: str
    hint: str | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class _Ring:
    """Ключи процесса из файла: шифр по всем строкам, номера строк и ключ отпечатка."""

    cipher: MultiFernet = field(repr=False)
    ids: tuple[str, ...]
    fingerprint_key: bytes = field(repr=False)


def _derive(raw: bytes, label: bytes) -> bytes:
    return hmac.new(raw, label, hashlib.sha256).digest()


def _ring() -> _Ring:
    """Ключи из файла мастер-ключа; строки файла во всех трёх видах и ключи Fernet — в вычистку."""
    lines = master_key.read(config.keys.master_key_file)
    fernets: list[Fernet] = []
    for line in lines:
        fernet_key = base64.urlsafe_b64encode(_derive(line.raw, FERNET_LABEL))
        for secret in (line.text, line.text.lower(), line.text.upper(), fernet_key.decode()):
            register_secret(secret)
        fernets.append(Fernet(fernet_key))
    ids = tuple(_derive(line.raw, MASTER_ID_LABEL).hex()[:MASTER_ID_CHARS] for line in lines)
    return _Ring(MultiFernet(fernets), ids, _derive(lines[0].raw, FINGERPRINT_LABEL))


def _plain(name: str, value: str) -> bytes:
    """Пара «имя и значение» одним JSON — открытый текст шифра и отпечатка."""
    pair = {"name": name, "value": value}
    return json.dumps(pair, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


def hint(value: str) -> str | None:
    """Подсказка для экрана: последние 4 знака значения от 20 знаков; у короткого — нет."""
    return value[-HINT_CHARS:] if len(value) >= HINT_FROM else None


def seal(name: str, value: str) -> Sealed:
    """Шифр значения текущей строкой файла, с номером мастер-ключа, отпечатком и подсказкой."""
    register_secret(value)
    ring = _ring()
    plain = _plain(name, value)
    token = ring.cipher.encrypt(plain).decode("ascii")
    fingerprint = hmac.new(ring.fingerprint_key, plain, hashlib.sha256).hexdigest()
    return Sealed(token, ring.ids[0], fingerprint[:FINGERPRINT_CHARS], hint(value))


def unseal(name: str, ciphertext: str, master_id: str) -> str:
    """Значение ключа `name` из записи базы; не открывается — `KeyUnreadable` со словом ключа."""
    ring = _ring()
    word = registry.key_word(name)
    if master_id not in ring.ids:
        raise KeyUnreadable(
            f"ключ {word} зашифрован мастер-ключом с номером {master_id}, его нет в файле"
        )
    pair = _pair(_opened(ring.cipher, ciphertext))
    if pair is None:
        raise KeyUnreadable(f"не расшифровывается: {word}")
    register_secret(pair[1])
    if pair[0] != name:
        raise KeyUnreadable(f"шифротекст другого ключа: {word}")
    return pair[1]


def _opened(cipher: MultiFernet, ciphertext: str) -> bytes | None:
    """Открытый текст или None. Текст не из ASCII даёт исключение кодека, а не `InvalidToken`
    (проба среза 0): исключения наружу не уходят, их текст и цепочка — тоже."""
    try:
        return cipher.decrypt(ciphertext.encode("ascii"))
    except (InvalidToken, UnicodeEncodeError):
        return None


def _pair(plain: bytes | None) -> tuple[str, str] | None:
    """Имя и значение из открытого текста; не JSON или не та форма — None."""
    if plain is None:
        return None
    try:
        data = json.loads(plain)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    name, value = data.get("name"), data.get("value")
    if isinstance(name, str) and isinstance(value, str):
        return name, value
    return None
