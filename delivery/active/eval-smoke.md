# Eval smoke — this shipment

Derived from spec acceptance. Run during verify.

- [ ] S0 — дайджесты образов из `decisions.md` (postgres, python, redis, uv):
      `docker buildx imagetools inspect <имя>:<тег>@<дайджест>` отвечает тем же дайджестом;
      версия в конфиге linux/amd64 — та же, что в строке decisions. Сетевое чтение реестров,
      без загрузки образа
- [ ] A23 — сканер слов и поясов, связанных с РФ и РБ (рабочий инструмент сессии, вне
      репозитория), по новым и изменённым файлам поставки: 0 совпадений; вывод — в
      `verify-report.md`

Строки pytest без Docker и живого прогона оракула добавляют срезы 1–4 (план, «Test / eval
strategy»).
