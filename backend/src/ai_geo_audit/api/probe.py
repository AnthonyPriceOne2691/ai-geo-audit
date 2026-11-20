"""Проба здоровья для compose: `python -m ai_geo_audit.api.probe` (A70).

`GET /api/health` на петле контейнера, порт — настройка `API_PORT`. Срок один на весь поход —
соединение, запрос и строка статуса, 3 с: защита, а не настройка, как предел здоровья Ч1.3а.
Код 0 только на ответ 200; иначе код 1 и одна строка причины — без адреса, порта и значений.
Только конфиг и стандартная библиотека: `curl` в образ не ставится.
"""

from __future__ import annotations

import socket
import sys
import time

try:
    from ai_geo_audit import config
except ValueError:  # ValidationError конфига — подкласс ValueError; в его тексте значение (L30)
    config = None

LIMIT_SECONDS = 3.0
#: Статусная строка ответа короче; дальше читать незачем.
HEAD_LIMIT = 4096
REQUEST = b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"


def check(port: int, limit: float = LIMIT_SECONDS) -> tuple[int, str]:
    """Код выхода и причина одной строкой; срок — один на соединение, запрос и ответ."""
    deadline = time.monotonic() + limit
    head = b""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=limit) as sock:
            sock.sendall(REQUEST)
            while b"\r\n" not in head and len(head) < HEAD_LIMIT:
                sock.settimeout(max(deadline - time.monotonic(), 0.001))
                chunk = sock.recv(512)
                if not chunk:
                    break
                head += chunk
    except TimeoutError:
        return 1, f"здоровье: нет ответа за {limit:g} с"
    except ConnectionRefusedError:
        return 1, "здоровье: порт закрыт — api не слушает"
    except OSError as error:
        return 1, f"здоровье: сбой соединения ({type(error).__name__})"
    words = head.split(b"\r\n", 1)[0].split()
    http = len(words) > 1 and words[0].startswith(b"HTTP/")
    status = words[1].decode("ascii", "replace") if http else ""
    if status == "200":
        return 0, "здоровье: ok"
    return 1, f"здоровье: ответ {status}" if status.isdigit() else "здоровье: ответ не HTTP"


def main() -> int:
    if config is None:
        code, reason = 1, "здоровье: негодная настройка — порт api неизвестен"
    else:
        code, reason = check(config.api.port)
    sys.stdout.write(reason + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
