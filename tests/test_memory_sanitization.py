r"""Verify that `_sanitize_json_for_cosmos` escapes unsafe sequences.

The sanitizer should:
- double stray backslashes so they survive JSON encoding
- neutralize partial Unicode escape sequences like ``\u``
- leave valid UTF-8 data such as emoji untouched
"""

import json
import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
from app.services.memory import _sanitize_json_for_cosmos


def test_sanitize_json_for_cosmos_handles_edges():
    r"""Strings containing raw backslashes, partial ``\u`` sequences, and emoji
    should be sanitised so that ``json.dumps`` succeeds and escape sequences
    are doubled where needed.
    """
    samples = {
        "raw": "bad \\z path",        # stray backslash
        "partial": "broken \\u12 seq",  # partial unicode escape
        "emoji": "rocket 🚀",            # valid UTF-8
    }

    sanitized = {k: _sanitize_json_for_cosmos(v) for k, v in samples.items()}

    # json.dumps should succeed for all sanitized strings
    for text in sanitized.values():
        dumped = json.dumps(text)
        assert isinstance(dumped, str)

    # Backslashes should be doubled in the sanitized result
    assert "\\\\z" in sanitized["raw"]
    assert "\\\\u12" in sanitized["partial"]

    # Emoji should remain unchanged
    assert sanitized["emoji"].endswith("🚀")

    # And the JSON representation should contain the doubled backslashes
    assert "\\\\\\\\z" in json.dumps(sanitized["raw"])
    assert "\\\\\\\\u12" in json.dumps(sanitized["partial"])
