"""Живые `env-init` и `put-secret` (6а-2, срез 4; A182, A183): настоящий `python3 -I deploy.py` от
root на машине джобы `stack` — корни сервера одноразовой машины, значения владельца выдуманы. Не
на машине CI или не от root — отказ до любой записи: скрипт пишет в `/srv` и `/etc`.

Шаг джобы: `sudo --preserve-env=GITHUB_ACTIONS python3 scripts/deploy_live.py`. Вывод — строки
«ок» или «КРАСНОЕ» с причиной, без значений: только имена, числа, права и коды (A183).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = Path("/srv/ai-geo-audit")
ETC = Path("/etc/ai-geo-audit")
API_UID = 10001
#: Файл владельца — выдуманные значения: домен `example.test`, адреса RFC 5737, токен-метка.
OWNER = {
    "SITE_DOMAIN": "example.test",
    "WEB_PORT": "18180",
    "FRONT_SUBNET": "198.51.100.0/28",
    "WEB_ADDRESS": "198.51.100.14",
    "NEIGHBOR_HEALTH_URLS": "http://127.0.0.1:18181/",
    "ALERT_TELEGRAM_BOT_TOKEN": "123456789:live-run-owner-token",
    "ALERT_TELEGRAM_CHAT_ID": "-1001234567890",
}
#: Образы боя пишет выкат (6в); для проверки наложения — выдуманные по дайджесту.
IMAGES = {
    "BACKEND_IMAGE": "registry.example.test/ai-geo-audit-backend@sha256:" + "0" * 64,
    "WEB_IMAGE": "registry.example.test/ai-geo-audit-web@sha256:" + "1" * 64,
}


class LiveError(Exception):
    """Красное шага: причина словами, без значений."""


def run(*argv: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — список без шелла, значения — окружением
        list(argv), capture_output=True, text=True, timeout=120, check=False, env=env
    )


def deploy(*args: str) -> subprocess.CompletedProcess[str]:
    return run(sys.executable, "-I", str(SERVICE / "deploy" / "deploy.py"), *args)


def rights(path: Path) -> tuple[int, int]:
    info = path.lstat()
    return stat.S_IMODE(info.st_mode), info.st_uid


def names(path: Path) -> list[str]:
    return [line.split("=", 1)[0] for line in path.read_text(encoding="utf-8").splitlines()]


def expect(ok: bool, why: str) -> None:
    if not ok:
        raise LiveError(why)


def step_env_init() -> str:
    """A182: env-init на чистых корнях — код 0, файл 600 root, ключ 400 uid api, каталоги 700."""
    done = deploy("env-init")
    expect(
        done.returncode == 0 and "создано: 9" in done.stdout, f"env-init — код {done.returncode}"
    )
    env, key = SERVICE / ".env", ETC / "master.key"
    got = {
        "файл": rights(env),
        "ключ": rights(key),
        "каталог": rights(ETC)[0],
        "входящие": rights(ETC / "incoming")[0],
    }
    want = {"файл": (0o600, 0), "ключ": (0o400, API_UID), "каталог": 0o700, "входящие": 0o700}
    expect(
        got == want, "права и владельцы не те: " + ", ".join(k for k in want if got[k] != want[k])
    )
    count = len(names(env))
    expect(count == 18, f"имён в файле сервера {count}, нужно 18")
    return "код 0, имён 18, файл 600 root, ключ 400 uid 10001, каталоги 700"


def step_put_secret() -> str:
    """A182: файл владельца из каталога входящих — код 0, «внесено: 7», входящий удалён."""
    incoming = ETC / "incoming" / "owner.env"
    fd = os.open(incoming, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write("".join(f"{name}={value}\n" for name, value in OWNER.items()))
    done = deploy("put-secret", str(incoming))
    expect(
        done.returncode == 0 and "внесено: 7" in done.stdout, f"put-secret — код {done.returncode}"
    )
    expect(not incoming.exists(), "входящий файл не удалён")
    text = (SERVICE / ".env").read_text(encoding="utf-8")
    missing = [name for name, value in OWNER.items() if f"{name}={value}\n" not in text]
    expect(not missing, "не внесены: " + ", ".join(missing))
    return "код 0, внесено 7, входящий удалён"


def step_second_env_init() -> str:
    """A182: второй env-init — отказ, файл сервера и ключ не тронуты."""
    kept = [SERVICE / ".env", ETC / "master.key"]
    before = [path.read_bytes() for path in kept]
    done = deploy("env-init")
    expect(done.returncode == 1, f"второй env-init — код {done.returncode}, нужен отказ")
    expect([path.read_bytes() for path in kept] == before, "файл сервера или ключ тронуты")
    return "отказ, файл и ключ прежние"


def step_tree_clean() -> str:
    """A183: `git status --porcelain` в каталоге сервиса — пусто: файл окружения и журнал хода —
    под игнором, ключ и входящие — вне дерева."""
    done = run("git", "-C", str(SERVICE), "status", "--porcelain")
    expect(done.returncode == 0 and not done.stdout.strip(), "в дереве сервиса есть изменения")
    return "git status пуст"


def step_compose_accepts() -> str:
    """A182: compose принимает файл сервера — все `${…:?}` основного файла заданы; с боевым
    наложением недостаёт только образов, их пишет выкат (6в)."""
    files = ["-f", str(SERVICE / "docker-compose.yml")]
    base = ["docker", "compose", "--project-directory", str(SERVICE), "--env-file"]
    base.append(str(SERVICE / ".env"))
    done = run(*base, *files, "config", "--quiet")
    expect(done.returncode == 0, "основной файл не принял файл сервера")
    prod = [*files, "-f", str(SERVICE / "docker-compose.prod.yml")]
    done = run(*base, *prod, "config", "--quiet", env=os.environ | IMAGES)
    expect(done.returncode == 0, "наложение не приняло файл сервера с образами")
    return "основной файл и наложение с образами — код 0"


STEPS = (
    ("A182 env-init на чистых корнях", step_env_init),
    ("A182 put-secret файла владельца", step_put_secret),
    ("A182 второй env-init", step_second_env_init),
    ("A183 дерево сервиса чистое", step_tree_clean),
    ("A182 compose принимает файл сервера", step_compose_accepts),
)


def main() -> int:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("отказ: только на машине джобы CI — скрипт пишет в /srv и /etc", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        print("отказ: нужен root — шаг джобы под sudo", file=sys.stderr)
        return 2
    if os.path.lexists(SERVICE) or os.path.lexists(ETC):
        print("отказ: корни сервера уже есть — машина не чистая", file=sys.stderr)
        return 2
    shutil.copytree(ROOT, SERVICE, symlinks=True)
    red = 0
    for title, step in STEPS:
        try:
            print(f"{title} — ок: {step()}")
        except (LiveError, OSError, subprocess.SubprocessError) as failure:
            red += 1
            # Причина своего отказа — слова без значений; у чужой ошибки — только её вид (A183).
            why = str(failure) if isinstance(failure, LiveError) else type(failure).__name__
            print(f"{title} — КРАСНОЕ: {why}")
    print(f"итог: шагов {len(STEPS)}, красных {red}")
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
