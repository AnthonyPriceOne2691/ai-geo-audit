"""Ветка-проба A98: ложное утверждение — джоба `tests` красная, тест назван."""


def test_a98_probe_false_assertion_names_the_test() -> None:
    """A98, ветка-проба: утверждение ложно нарочно."""
    assert sum([1]) == 2
