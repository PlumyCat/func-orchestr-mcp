from app.services.memory import _sanitize_text_for_cosmos, _final_cosmos_scrub


def test_unpaired_surrogate_removed():
    bad = "\ud83d"  # lone high surrogate
    cleaned = _sanitize_text_for_cosmos(bad)
    assert cleaned == ""


def test_valid_surrogate_pair_preserved():
    emoji = "\U0001F600"  # 😀
    cleaned = _sanitize_text_for_cosmos(emoji)
    assert cleaned == emoji


def test_doc_with_unpaired_surrogate_is_sanitized():
    doc = {"text": "\ud83d"}
    cleaned = _final_cosmos_scrub(doc)
    assert cleaned["text"] == ""

