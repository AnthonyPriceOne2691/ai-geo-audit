# Eval smoke — this shipment

Derived from spec acceptance. Run during verify.

- [ ] A30, A31 — `docker compose -f docker-compose.dev.yml up -d db`, затем
      `cd backend && uv run pytest -q`: все тесты зелёные, у каждой строки примеров свой тест;
      собрано, прошло и пропущено — числом, пропущенных 0
- [ ] A31 — pytest подпроцессом с грязным окружением: `APP_ENV=prod` и `app_env=prod`,
      `ENV_FILE`, ключи провайдеров, `PGSSLMODE=require`, `HTTPS_PROXY` — внутри режим `dev`,
      проба соединяется с базой
- [ ] A31 — pytest подпроцессом из каталога с `.env` разработчика (`APP_ENV=prod`, пароль и
      ключ-метки): внутри `dev`, меток в выводе нет
- [ ] A23 — сканер слов и поясов, связанных с РФ и РБ (рабочий инструмент сессии, вне
      репозитория), по новым и изменённым файлам поставки: в коде 0 совпадений; вывод — в
      `verify-report.md`
