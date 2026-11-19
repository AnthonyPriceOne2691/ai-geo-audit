# Eval smoke — this shipment

Derived from spec acceptance. Run during verify.

- [ ] A15–A18, A22 — `docker compose -f docker-compose.dev.yml up -d db`, затем
      `cd backend && uv run pytest -q`: все тесты зелёные, у каждой строки примеров свой тест;
      собрано, прошло и пропущено — числом, пропущенных 0
- [ ] A17, A22 — `cd backend && DATABASE_URL=<адрес пустой базы> uv run alembic upgrade head`
      дважды: `alembic_version` = `0001`, второй прогон ничего не меняет, код выхода 0
- [ ] A23 — сканер слов и поясов, связанных с РФ и РБ (рабочий инструмент сессии, вне
      репозитория), по новым и изменённым файлам поставки: 0 совпадений; вывод — в
      `verify-report.md`
