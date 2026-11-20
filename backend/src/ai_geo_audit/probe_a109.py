"""Ветка-проба A109: функция, которую тест зовёт без утверждений, — мутанты выживают."""


def clamp(value: int, low: int, high: int) -> int:
    """Значение в границах от `low` до `high`."""
    return max(low, min(value, high))
