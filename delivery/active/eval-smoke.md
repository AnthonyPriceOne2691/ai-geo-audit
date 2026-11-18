# Eval smoke — this shipment

Derived from spec acceptance. Run during verify.

- [ ] A1–A9 — `docker compose -f docker-compose.dev.yml up -d db`, затем
      `cd backend && uv run pytest -q`: все тесты зелёные, у каждого примера свой тест
- [ ] A6 — миграции с нуля в чистом клоне: `alembic upgrade head`, повторный `upgrade head`,
      `downgrade base`, снова `upgrade head`; вывод — блоком «Исполнение рисковых путей» в
      `verify-report.md`
- [ ] A10 — сканер слов и поясов, связанных с РФ и РБ (рабочий инструмент сессии, вне
      репозитория), по новым и изменённым файлам поставки: 0 совпадений; вывод — в
      `verify-report.md`
