import pytest
from app.services.memory import _final_cosmos_scrub


def test_neutralize_malformed_unicode_sequences():
    doc = {
        "id": "conv_1",
        "messages": [
            {"role": "user", "content": r"Hello \u here"},
            {"role": "assistant",
                "content": r"Partial unicode: \\u123 OK, bad: \u12 more, and hex short: \u1 end"},
        ]
    }
    cleaned = _final_cosmos_scrub(doc)
    # After scrub, no single backslash malformed patterns should remain
    import re
    bad_patterns = [
        r"(?<!\\)\\u(?![0-9a-fA-F]{4})",
        r"(?<!\\)\\u[0-9a-fA-F]{1,3}(?=\b|[^0-9a-fA-F])",
    ]
    for msg in cleaned["messages"]:
        text = msg["content"]
        for pat in bad_patterns:
            assert re.search(
                pat, text) is None, f"Pattern {pat} still present in '{text}'"
