# Eval smoke — this shipment

Derived from spec acceptance. Run during verify.

- [ ] A9–A13 — `docker compose -f docker-compose.dev.yml up -d db`, затем
      `cd backend && uv run pytest -q`: все тесты зелёные, у каждой строки примеров свой тест
- [ ] A12, первая строка — тот же прогон при выключенном Postgres: одно сообщение с командой
      `docker compose -f docker-compose.dev.yml up -d db`, без пропусков
- [ ] A14 — сканер слов и поясов, связанных с РФ и РБ (рабочий инструмент сессии, вне
      репозитория), по новым и изменённым файлам поставки: 0 совпадений; вывод — в
      `verify-report.md`
