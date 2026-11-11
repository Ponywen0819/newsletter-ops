import pytest

from newsletter_shared.kg_flags import append, read


def test_last_flag_wins(tmp_path):
    p = tmp_path / "kg_flags.jsonl"
    assert read(p) == {}
    append("a" * 16, "wrong", p)
    append("b" * 16, "trusted", p)
    append("a" * 16, "", p)
    assert read(p) == {"b" * 16: "trusted"}


def test_bad_flag(tmp_path):
    with pytest.raises(ValueError):
        append("c" * 16, "maybe", tmp_path / "kg_flags.jsonl")
