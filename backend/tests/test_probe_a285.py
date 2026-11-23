"""Проба A285 «Скорости CI» — только в ветке-пробе, в main не идёт: тест области гейта мутаций."""

from __future__ import annotations

from ai_geo_audit import probe_a285


def test_a285_probe_area_function() -> None:
    """A285: функция области пробы — каждая порча меняет итог; сбой копии делают переменные шага."""
    assert probe_a285.a285(3, 4) == 20
