import pytest
from app.services.memory import _final_cosmos_scrub


def test_neutralize_malformed_unicode_sequences():
    doc = {
        "id": "conv_1",
        "messages": [
            {
                "role": "user",
                "content": r"Hello \u here and bad hex \x1",
            },
            {
                "role": "assistant",
                "content": r"Upper \U0000ZZZZ and short \u12",
            },
        ],
    }
    cleaned = _final_cosmos_scrub(doc)
    # After scrub, no single backslash malformed patterns should remain
    import re
    bad_patterns = [
        r"(?<!\\)\\u(?![0-9a-fA-F]{4})",
        r"(?<!\\)\\x(?![0-9a-fA-F]{2})",
        r"(?<!\\)\\U(?![0-9a-fA-F]{8})",
    ]
    for msg in cleaned["messages"]:
        text = msg["content"]
        for pat in bad_patterns:
            assert re.search(pat, text) is None, f"Pattern {pat} still present in '{text}'"
