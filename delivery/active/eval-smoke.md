# Eval smoke — this shipment

Derived from spec acceptance. Run during verify.

- [ ] A8, A11, A19–A21, A24–A29 — `docker compose -f docker-compose.dev.yml up -d db`, затем
      `cd backend && uv run pytest -q`: все тесты зелёные, у каждой строки примеров свой тест;
      собрано, прошло и пропущено — числом, пропущенных 0
- [ ] A19 — четыре `alembic upgrade head` одновременно по пустой схеме: все четыре — код 0, в
      `alembic_version` одна строка
- [ ] A23 — сканер слов и поясов, связанных с РФ и РБ (рабочий инструмент сессии, вне
      репозитория), по новым и изменённым файлам поставки: в коде 0 совпадений; вывод — в
      `verify-report.md`

A30 и A31 (срез 5) сдаются в Ч1.2г — контрольная точка T11.
