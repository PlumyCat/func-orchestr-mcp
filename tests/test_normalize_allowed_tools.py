import pytest

from app.services.tools import normalize_allowed_tools


@pytest.mark.parametrize(
    "raw, expected",
    [
        (["a", "b"], ["a", "b"]),
        ("a, b", ["a", "b"]),
        ('["a", 1]', ["a", "1"]),
    ],
)
def test_normalize_allowed_tools_valid_inputs(raw, expected):
    assert normalize_allowed_tools(raw) == expected


@pytest.mark.parametrize("raw", [None, "", {}, 123, "[invalid]"])
def test_normalize_allowed_tools_invalid_inputs(raw):
    assert normalize_allowed_tools(raw) is None
