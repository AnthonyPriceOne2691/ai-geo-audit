# Eval smoke — this shipment

Derived from spec acceptance. Run during verify.

- [ ] A32–A36, A38–A47 — `docker compose -f docker-compose.dev.yml up -d db`, затем
      `cd backend && uv run pytest -q`: все тесты зелёные, у каждой строки примеров свой тест;
      собрано, прошло и пропущено — числом, пропущенных 0. A37 — в Ч1.3б
- [ ] S1 — база разработки поднята; `cd backend && uv run python -m ai_geo_audit.api`; `curl -si`
      к `/api/health?token=s3cr3t-in-url-123456`: 200, `{"status":"ok"}`, нет `server`, есть
      `x-request-id`; в выводе сервера только наш формат с `+0000`, одна строка доступа без
      строки запроса; `curl -si /api/nope` — 403 `signed-out`. Предел — 30 с. Если сработал
      шов, строка доступа переезжает в живые прогоны Ч1.3б (plan, «Ч1.3б и шов»)
- [ ] S2 — тот же сервер, база остановлена: 503 `unavailable` не дольше 4 с
- [ ] S3 — `SIGTERM` серверу: «Application shutdown complete» нашим форматом, процесс кончается
      тем же сигналом. Предел — 25 с
- [ ] A23 — сканер слов и поясов, связанных с РФ и РБ (рабочий инструмент сессии, вне
      репозитория), по новым и изменённым файлам поставки и `uv.lock`: в коде 0 совпадений;
      вывод — в `verify-report.md`
