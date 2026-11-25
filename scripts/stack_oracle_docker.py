"""Исполнитель команд Docker оракула стека: предел на каждую команду и учёт своего для уборки.

Вынесен из `stack_oracle.py` («Учётки-1», срез 2): файл оракула упёрся в предел 500 строк.
Импорт в одну сторону: `stack_oracle.py` берёт отсюда `Docker` и `LIMITS`.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Any

from stack_oracle_compose import StepError

#: Пределы команд, секунды: зависший шаг называется своим пределом, а не висит.
LIMITS = {"docker": 30, "build": 1200, "run": 120, "export": 300, "up": 300}


@dataclass
class Docker:
    """Один исполнитель команд Docker: предел на каждую команду и учёт своего для уборки."""

    containers: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)

    def result(
        self, *args: str, limit: str = "run", **kw: Any
    ) -> subprocess.CompletedProcess[bytes]:
        """Команда Docker под пределом; `stdin` — байты на вход, `env` — окружение команды."""
        seconds, stdin = LIMITS[limit], kw.pop("stdin", None)
        try:
            return subprocess.run(  # noqa: S603 -- docker и аргументы оракула, без оболочки
                ["docker", *args],
                input=stdin,
                capture_output=True,
                timeout=seconds,
                check=False,
                **kw,
            )
        except subprocess.TimeoutExpired:
            raise StepError(f"docker {' '.join(args[:2])} не уложился в {seconds} с") from None

    def __call__(self, *args: str, limit: str = "run", check: bool = True, **kw: Any) -> str:
        done = self.result(*args, limit=limit, **kw)
        if check and done.returncode:
            tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-6:]
            raise StepError(f"docker {args[0]} — код {done.returncode}: {' | '.join(tail)}")
        return done.stdout.decode("utf-8", "replace")

    def cleanup(self) -> list[str]:
        """Убрать свои контейнеры и образы; что не убралось — назвать."""
        plan = [
            (("rm", "-f", "-v", c), ("ps", "-aq", "--filter", f"name=^{c}$"), c)
            for c in dict.fromkeys(self.containers)
        ]
        plan += [
            (("image", "rm", "-f", i), ("image", "ls", "-q", i), i)
            for i in dict.fromkeys(self.images)
        ]
        left = []
        for remove, probe, what in plan:
            try:
                self(*remove, limit="docker", check=False)
                if self(*probe, limit="docker", check=False).strip():
                    left.append(what)
            except StepError:
                left.append(what)
        return left
