# Eval smoke — this shipment

Derived from spec acceptance. Run during verify.

- [ ] A37, A48–A58 — `docker compose -f docker-compose.dev.yml up -d db`, затем
      `cd backend && uv run pytest -q`: все тесты зелёные, у каждой строки примеров свой тест;
      собрано, прошло и пропущено — числом, пропущенных 0
- [ ] S1–S3 — как в Ч1.3а и Ч1.3; в S1 — ещё по одной копии `cache-control: no-store` и
      `x-content-type-options: nosniff`, ни одного заголовка `access-control-*`
- [ ] S4 — сервер разработки; сырой сокет шлёт заголовки `POST /api/health` с
      `Content-Length: 1048577` — 413 `too-large` до тела; `curl --data-binary` ровно 1 048 576
      байт на `POST /api/health` — 405. Предел — 30 с
- [ ] S5 — база — сервер на петле, который принимает соединения и молчит: сокет с `listen(64)`
      без `accept()`; 40 параллельных `GET /api/health` — 32 ответа 503 `unavailable` через 3 с
      и 8 ответов 503 `busy` сразу, с `Retry-After: 60`; в выводе — 8 записей WARNING «сервис
      занят». Предел — 30 с
- [ ] A23 — сканер слов и поясов, связанных с РФ и РБ (рабочий инструмент сессии, вне
      репозитория), по новым и изменённым файлам поставки: в коде 0 совпадений; вывод — в
      `verify-report.md`
