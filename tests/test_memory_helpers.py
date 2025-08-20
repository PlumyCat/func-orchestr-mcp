import app.services.memory as memory
from app.services.memory import _sanitize_container_name, _derive_short_title_from_text


def test_sanitize_container_name_replaces_invalid_chars():
    raw = "user/with spaces?*%"
    result = _sanitize_container_name(raw)
    assert result == "mem_user_with_spaces___"


def test_sanitize_container_name_truncates_to_255_chars():
    raw = "a" * 300
    result = _sanitize_container_name(raw)
    assert len(result) == 255
    assert result.startswith("mem_")
    assert result == "mem_" + "a" * 251


def test_derive_short_title_normalizes_and_capitalizes():
    text = "hello\nworld  "
    result = _derive_short_title_from_text(text)
    assert result == "Hello world"


def test_derive_short_title_limits_max_words():
    text = "one two three four five six seven eight nine"
    result = _derive_short_title_from_text(text)
    assert result == "One two three four five six seven eight"


def test_derive_short_title_limits_max_length():
    text = "a" * 100
    result = _derive_short_title_from_text(text)
    assert result == "A" + "a" * 58 + "…"


def test_derive_short_title_empty_returns_generic(monkeypatch):
    monkeypatch.setattr(memory.time, "strftime", lambda fmt, _: "2023-07-05 12:34")
    result = _derive_short_title_from_text("")
    assert result == "Conversation 2023-07-05 12:34"
