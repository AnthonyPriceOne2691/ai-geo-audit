"""Шаг оракула «живой `deploy.py check`» (6а-3, T3; A185, A183): настоящий `python3 -I deploy.py
check` от root на машине джобы `stack`, пока стек прогона жив, — до шагов защиты и уборки. Для
скрипта выката стек прогона — «сосед» с нашей приставкой под другим проектом.

Корни сервера, файл владельца и вымышленного соседа `/srv/nb-oracle` заводит и снимает помощник
`deploy_check_live.py`, команды скрипта — `deploy.py` корня сервиса; оба — `sudo -n python3 -I`,
значения — окружением через `--preserve-env`, не аргументами (A183). Сначала значения, что
сталкиваются со стеком прогона: имена «ок», `WEB_PORT` занят и объявлен у соседа, «сверить»,
`FRONT_SUBNET` — отказ; затем чистые: те же строки «ок». Строку ресурсов шаг не судит — у раннера
свои память и диск, — но код выхода не 0 тогда и только тогда, когда отказ хоть в одной строке.
Корни снимаются в конце и на красном. Вне CI — пропуск словами, без записи.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from stack_oracle_compose import SUBNETS, StepError, busy_on, slot, web_address, web_slot

if TYPE_CHECKING:
    from stack_oracle import Run

HELPER = str(Path(__file__).resolve().parent / "deploy_check_live.py")
DEPLOY = "/srv/ai-geo-audit/deploy/deploy.py"
INCOMING = "/etc/ai-geo-audit/incoming/owner.env"
#: Значения владельца, кроме порта, подсети и адресов, — выдуманы: домен RFC 2606, токен-метка.
OWNER = {
    "SITE_DOMAIN": "example.test",
    "ALERT_TELEGRAM_BOT_TOKEN": "123456789:live-check-owner-token",
    "ALERT_TELEGRAM_CHAT_ID": "-1001234567891",
}
SVERIT = "у соседа порт задан переменной — сверить"
#: Строки хода `deploy.py check`, а не темы: начало, метка замка, счёт и итог.
SESSION = frozenset({"check", "устаревшая метка", "проверено строк", "итог"})


def sudo(*argv: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """`sudo -n python3 -I <скрипт> <режим>`: без пароля, иначе код не 0; значения — окружением
    через `--preserve-env=<имена>`, не `sudo env X=…` и не аргументами: argv видны всем (A183)."""
    extra = env or {}
    command = ["sudo", "-n", "--preserve-env=" + ",".join(["GITHUB_ACTIONS", *extra])]
    try:
        return subprocess.run(  # noqa: S603 — список без оболочки, значения — окружением
            [*command, "python3", "-I", *argv],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
            env=os.environ | extra,
        )
    except (OSError, subprocess.SubprocessError) as failure:
        raise StepError(f"sudo {argv[1]}: {type(failure).__name__}") from None


def must(done: subprocess.CompletedProcess[str], what: str) -> None:
    if done.returncode:
        raise StepError(f"{what}: код {done.returncode}")


def free_slot(run: Run) -> tuple[int, str]:
    """Чистые значения: порт web и подсеть `front` из ряда A82 от имени шага — мимо подсетей сетей
    докера машины, стека прогона тоже."""
    ids = run.docker("network", "ls", "-q", limit="docker").split()
    show = ["network", "inspect", "--format", SUBNETS, *ids]
    nets = run.docker(*show, limit="docker").split() if ids else []
    return web_slot(f"{run.name}-check", busy_on(nets))


def check_with(port: int, subnet: str, url: str) -> tuple[int, str, dict[str, str]]:
    """Файл владельца с этими значениями, `put-secret`, `deploy.py check`: код, вывод, значения."""
    values = OWNER | {"WEB_PORT": str(port), "FRONT_SUBNET": subnet}
    values |= {"WEB_ADDRESS": web_address(subnet), "NEIGHBOR_HEALTH_URLS": url}
    must(sudo(HELPER, "owner", env=values), "файл владельца")
    must(sudo(DEPLOY, "put-secret", INCOMING), "put-secret")
    done = sudo(DEPLOY, "check")
    return done.returncode, done.stdout + done.stderr, values


def judge(label: str, answer: tuple[int, str, dict[str, str]], *, collision: bool) -> int:
    """Строки одного `check`: имена, первая строка порта, «сверить», первая строка подсети; код
    — по строкам (отказ — «отказ — …», у снимка соседа — «— красное:»; ресурсы тоже); значений
    в выводе нет. Не то — красное словами, без значений; иначе — число судимых строк."""
    code, out, values = answer
    rows = [line.split(" ", 1)[-1].partition(": ") for line in out.splitlines()]
    lines = [(topic, text) for topic, _sep, text in rows if topic not in SESSION]
    said = {t: [text for topic, text in lines if topic == t] for t in ("имена", "порт", "подсеть")}
    port, net = (said[topic][0] if said[topic] else "" for topic in ("порт", "подсеть"))
    wrong = [] if said["имена"] == ["ок"] else ["имена — не «ок»"]
    if collision:
        taken = ("занят", "объявлен у соседа")
        wrong += [f"порт — нет «{w}»" for w in taken if f"WEB_PORT {w}" not in port]
        wrong += [] if "FRONT_SUBNET пересекается" in net else ["подсеть — нет отказа FRONT_SUBNET"]
    else:
        wrong += [
            f"{topic} — не «ок»" for topic in ("порт", "подсеть") if said[topic][:1] != ["ок"]
        ]
    wrong += [] if SVERIT in said["порт"] else ["нет «сверить»"]
    refused = sum(text.startswith("отказ — ") or " — красное: " in text for _t, text in lines)
    wrong += [f"код {code}, а отказов в строках {refused}"] if bool(code) != bool(refused) else []
    if leaked := [name for name, value in values.items() if value in out]:
        wrong.append("значения в выводе check: " + ", ".join(leaked))
    if wrong:
        raise StepError(f"{label}: {'; '.join(wrong)}")
    return 4


def step_live_check(run: Run) -> str:
    """A185, A183: корни, `env-init`, столкновение со стеком прогона, чистые значения, уборка —
    и на красном; причина красного до уборки не теряется."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return "пропуск: не машина CI — шаг пишет в /srv и /etc от root"
    (port, subnet), (clean_port, clean_subnet) = slot(run), free_slot(run)
    must(sudo(HELPER, "roots", env={"NB_DECLARED_PORT": str(port)}), "корни сервера")
    red = ""
    try:
        must(sudo(DEPLOY, "env-init"), "env-init")
        url = f"http://127.0.0.1:{port}/"
        hit = check_with(port, subnet, url)
        judged = judge("столкновение", hit, collision=True)
        calm = check_with(clean_port, clean_subnet, url)
        judged += judge("чистые значения", calm, collision=False)
    except StepError as failure:
        red = f"{failure}; "
        raise
    finally:
        left = sudo(HELPER, "clean").returncode
        if left:
            raise StepError(f"{red}уборка корней: код {left}")
    return (
        f"судимых строк {judged}; столкновение со стеком прогона — код {hit[0]}: имена ок, порт "
        "занят и объявлен у соседа, сверить, подсеть — отказ; чистые значения — имена, порт и "
        f"подсеть ок, сверить, код {calm[0]} по строкам; значений в выводе 0"
    )
