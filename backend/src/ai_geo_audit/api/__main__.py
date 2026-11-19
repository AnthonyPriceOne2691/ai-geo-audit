"""`python -m ai_geo_audit.api`: проверка старта и журналы, затем uvicorn с `server_settings()`,
приложение — фабрикой. Отказы входа — словами, без трассировки, как у `migrate`: негодная
настройка ловится на импорте конфига, недостающая в бою — проверкой старта до сервера.
"""

from __future__ import annotations

from pydantic import ValidationError

from ai_geo_audit.refusal import invalid_settings

try:
    import uvicorn

    from ai_geo_audit.api.app import server_settings
    from ai_geo_audit.config.startup_checks import ConfigError, check_startup
    from ai_geo_audit.logs.setup import setup_logging
except ValidationError as invalid:
    raise SystemExit(invalid_settings("api", invalid)) from None


def main() -> None:
    """Проверка старта, журналы, сервер."""
    try:
        check_startup("api")
    except ConfigError as refusal:
        raise SystemExit(str(refusal)) from None
    setup_logging()
    uvicorn.run(**server_settings())  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
