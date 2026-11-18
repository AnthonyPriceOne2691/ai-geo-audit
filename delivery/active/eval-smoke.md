# Eval smoke — this shipment

Derived from spec acceptance. Run during verify.

- [ ] A1–A6, A8 — `cd backend && uv run pytest -q`: все тесты зелёные, у каждого примера свой тест
- [ ] A7 — сканер слов и поясов, связанных с РФ и РБ (рабочий инструмент сессии, вне
      репозитория), по `backend/src`, `backend/tests`, `backend/pyproject.toml` и
      `.env.example`: 0 совпадений; вывод — в `verify-report.md`
