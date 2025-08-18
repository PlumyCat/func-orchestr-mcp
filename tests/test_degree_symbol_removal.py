from app.services.memory import _sanitize_text_for_cosmos


def test_degree_symbol_removed():
    s = "Temp 23°C and 73°F"
    cleaned = _sanitize_text_for_cosmos(s)
    assert "°" not in cleaned
    # Ensure other characters preserved
    assert "23" in cleaned and "C" in cleaned and "F" in cleaned
