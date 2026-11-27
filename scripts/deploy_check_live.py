"""Помощник root-части живого `deploy.py check` (6а-3, T3; A185, A183): корни сервера одноразовой
машины джобы `stack` — копия клона в `/srv/ai-geo-audit` и вымышленный сосед `/srv/nb-oracle`
(decisions), файл владельца в каталоге входящих и уборка. Зовёт шаг оракула
`stack_oracle_check.py`: `sudo -n --preserve-env=<имена> python3 -I <этот файл> <режим>`; значения —
только окружением (A183). Не на машине CI или не от root — отказ до любой записи. Снимает только
заведённое им самим: метка — в каталоге соседа; корень или сосед уже есть — отказ, ничего не
тронуто.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE, ETC = Path("/srv/ai-geo-audit"), Path("/etc/ai-geo-audit")
NEIGHBOR, MARK = Path("/srv/nb-oracle"), ".deploy-check-live"
#: Имена «задаёт владелец» блока «сервер» образца `.env.example`: значения — из окружения.
OWNER = (
    "SITE_DOMAIN",
    "WEB_PORT",
    "FRONT_SUBNET",
    "WEB_ADDRESS",
    "NEIGHBOR_HEALTH_URLS",
    "ALERT_TELEGRAM_BOT_TOKEN",
    "ALERT_TELEGRAM_CHAT_ID",
)
#: Порт, что вымышленный сосед объявляет строкой портов, — из окружения.
DECLARED = "NB_DECLARED_PORT"
#: Файл compose соседа: порт web — строкой с умолчанием `{port}`, порт api — переменной без
#: умолчания (для `deploy.py check` — «сверить»).
COMPOSE = """\
services:
  web:
    ports:
      - "127.0.0.1:${NB_WEB_PORT:-{port}}:80"
  api:
    ports:
      - "127.0.0.1:${NB_API_PORT}:8000"
"""


def refuse(why: str) -> int:
    print(f"отказ: {why}", file=sys.stderr)
    return 2


def roots() -> int:
    """Корень сервиса — копия клона, сосед — файл compose и метка; свой `/etc` заводит
    `env-init`. Корень, `/etc` или сосед уже есть — отказ; сбой — заведённое снято."""
    if any(os.path.lexists(path) for path in (SERVICE, ETC, NEIGHBOR)):
        return refuse("корни сервера или каталог соседа уже есть — машина не чистая")
    port = os.environ.get(DECLARED, "")
    if not port.isdigit():
        return refuse(f"нет в окружении: {DECLARED} — порт числом")
    made: list[Path] = []
    try:
        made.append(SERVICE)
        shutil.copytree(ROOT, SERVICE, symlinks=True)
        made.append(NEIGHBOR)
        NEIGHBOR.mkdir()
        compose = COMPOSE.replace("{port}", port)
        (NEIGHBOR / "docker-compose.yml").write_text(compose, encoding="utf-8")
        (NEIGHBOR / MARK).write_text("заведено помощником живого deploy.py check\n", "utf-8")
    except OSError as failure:
        for path in made:
            shutil.rmtree(path, ignore_errors=True)
        return refuse(f"корни не заведены: {type(failure).__name__} — заведённое снято")
    return 0


def owner() -> int:
    """Файл владельца в каталоге входящих, права 600: имена «задаёт владелец», значения — из
    окружения, не из аргументов (A183); нет значения — отказ с именем; файл есть — отказ."""
    if missing := [name for name in OWNER if not os.environ.get(name)]:
        return refuse(f"нет в окружении: {', '.join(missing)}")
    path = ETC / "incoming" / "owner.env"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write("".join(f"{name}={os.environ[name]}\n" for name in OWNER))
    return 0


def clean() -> int:
    """Корень сервиса, свой `/etc` и сосед — только при метке помощника у соседа; без неё —
    отказ, ничего не тронуто. Сосед с меткой — последним."""
    if not (NEIGHBOR / MARK).is_file():
        return refuse("корни не заведены шагом — метки помощника нет, ничего не тронуто")
    for path in (SERVICE, ETC, NEIGHBOR):
        if os.path.lexists(path):
            shutil.rmtree(path)
    return 0


MODES: dict[str, Callable[[], int]] = {"roots": roots, "owner": owner, "clean": clean}


def main(argv: list[str]) -> int:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return refuse("только на машине джобы CI — помощник пишет в /srv и /etc")
    if os.geteuid() != 0:
        return refuse("нужен root — sudo -n из шага оракула")
    if len(argv) != 1 or argv[0] not in MODES:
        return refuse("режим — roots, owner или clean")
    try:
        return MODES[argv[0]]()
    except OSError as failure:
        return refuse(f"{argv[0]}: {type(failure).__name__}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
