# Eval smoke — this shipment

Derived from spec acceptance. Run during verify.

- [ ] A32–A35, A42, A44, A45 — `docker compose -f docker-compose.dev.yml up -d db`, затем
      `cd backend && uv run pytest -q`: все тесты зелёные, у каждой строки примеров свой тест;
      собрано, прошло и пропущено — числом, пропущенных 0. A36, A38–A41, A43, A46, A47 и живые
      прогоны S1, S2 — поставка «Здоровье и журналы запроса» (шов)
- [ ] S3 — `SIGTERM` серверу: «Application shutdown complete» нашим форматом, процесс кончается
      тем же сигналом. Предел — 25 с
- [ ] A23 — сканер слов и поясов, связанных с РФ и РБ (рабочий инструмент сессии, вне
      репозитория), по новым и изменённым файлам поставки и `uv.lock`: в коде 0 совпадений;
      вывод — в `verify-report.md`
