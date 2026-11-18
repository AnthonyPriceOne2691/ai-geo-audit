"""Общая база настроек. Окружение читается только в пакете `config`: опечатка в имени
переменной вне типизированного конфига ловится только в работе, и обычно в бою.
"""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict

#: Откуда читать `.env`. Тесты ставят пустой файл: их результат не зависит от машины.
ENV_FILE = os.environ.get("ENV_FILE", ".env")


class Settings(BaseSettings):
    """База доменов: `.env`, регистр не важен, чужие ключи пропускаются (в одном `.env`
    живут переменные всех доменов).

    `populate_by_name`: конструктор принимает и имя поля, а не только имя переменной. В
    старых pydantic-settings без флага значение молча терялось; в 2.16 принимается и так.
    Флаг стоит явно, чтобы поведение не зависело от версии.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )
