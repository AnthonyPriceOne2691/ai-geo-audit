"""Манифест настоящего файла справочника (A303, A308; PHASES:300): формат, строк, sha256 байтов,
дата снимка. Тексты справочника в публичный репозиторий не идут — идёт манифест: ревью сверяет хеш,
через год по нему видно, какой файл в бою; новый файл — новый манифест одним коммитом. До файла
ведущей хеш и дата пустые — `check` отказывает «манифест не заполнен»; у фикстуры тестов манифест
свой — `--manifest`, только не в бою."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_geo_audit.reference.file import FORMAT, FileRefusedError, ReferenceFile


class Manifest(BaseModel):
    """Манифест: sha256 — 64 знака `0-9a-f`, дата — ISO; до файла ведущей оба пустые."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    format: int
    rows: int = Field(ge=1)
    sha256: str = Field(pattern=r"^([0-9a-f]{64})?$")
    source_date: str = Field(pattern=r"^([0-9]{4}-[0-9]{2}-[0-9]{2})?$")

    @model_validator(mode="after")
    def _together(self) -> Self:
        if bool(self.sha256) != bool(self.source_date):
            raise ValueError("хеш и дата снимка — вместе: оба пустые или оба заполнены")
        return self


#: Манифест файла ведущей; до файла — 58 строк (ТЗ:384), хеш и дата пустые. Хеш данных — не
#: секрет: пометка для сканера строкой выше и строка в decisions (конституция, п. 11; L61).
MANIFEST: Final = Manifest(
    format=FORMAT,
    rows=58,
    # pragma: allowlist nextline secret
    sha256="",
    source_date="",
)


def load(path: Path) -> Manifest:
    """Манифест из JSON того же вида — для `--manifest` в dev и test."""
    try:
        return Manifest.model_validate(json.loads(path.read_bytes()))
    except (OSError, ValueError):  # ValidationError и ошибка разбора JSON — тоже ValueError
        raise FileRefusedError(
            ["манифест не читается или не того вида: format, rows, sha256, source_date"]
        ) from None


def mismatches(found: ReferenceFile, manifest: Manifest) -> list[str]:
    """Расхождения файла с манифестом; пустой хеш — манифест не заполнен."""
    if not manifest.sha256:
        return ["манифест не заполнен — файла ведущей в нём ещё нет"]
    why = []
    if len(found.rows) != manifest.rows:
        rows = f"строк {len(found.rows)}, в манифесте {manifest.rows}"
        why.append(f"файл не совпадает с манифестом: {rows}")
    if found.sha256 != manifest.sha256:
        why.append("файл не совпадает с манифестом: sha256 другой — манифест описывает другой файл")
    return why
