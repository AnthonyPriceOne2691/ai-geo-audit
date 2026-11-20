"""Ветка-проба A109: тест без утверждений — гейт мутаций обязан краснеть."""

from ai_geo_audit.probe_a109 import clamp


def test_a109_probe_calls_without_asserting() -> None:
    """A109, ветка-проба: зовёт и ничего не утверждает."""
    clamp(5, 0, 10)
