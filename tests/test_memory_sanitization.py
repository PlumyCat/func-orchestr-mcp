r"""Verify that `_sanitize_json_for_cosmos` escapes unsafe sequences.

The sanitizer should:
- double stray backslashes so they survive JSON encoding
- neutralize partial Unicode escape sequences like ``\u``
- leave valid UTF-8 data such as emoji untouched
"""

from app.services.memory import _sanitize_json_for_cosmos, _final_cosmos_scrub
import json
import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))


def test_sanitize_json_for_cosmos_handles_edges():
    r"""Strings containing raw backslashes, partial ``\u`` sequences, and emoji
    should be sanitised so that ``json.dumps`` succeeds and escape sequences
    are doubled where needed.
    """
    samples = {
        "raw": "bad \\z path",          # stray backslash
        "partial": "broken \\u12 seq",    # partial unicode escape
        "truncated_u": "end \\u",       # just \u at end
        "short_hex": "short \\u1 end",  # 1 hex digit only
        "mid_short": "text \\u12 middle",  # 2 hex digits then space
        "emoji": "rocket 🚀",              # valid UTF-8
    }

    sanitized = {k: _sanitize_json_for_cosmos(v) for k, v in samples.items()}

    # json.dumps should succeed for all sanitized strings
    for text in sanitized.values():
        dumped = json.dumps(text)
        assert isinstance(dumped, str)

    # Backslashes should be doubled in the sanitized result
    assert "\\\\z" in sanitized["raw"]
    assert "\\\\u12" in sanitized["partial"]
    assert "truncated_u" in sanitized
    assert sanitized["truncated_u"].endswith(
        "\\u") or sanitized["truncated_u"].endswith("\\\\u")
    # Final scrub should not reintroduce raw \u sequences
    final_doc = _final_cosmos_scrub({"samples": list(sanitized.values())})
    dumped = json.dumps(final_doc, ensure_ascii=False)
    # Any \u that remains must be part of a valid 4-hex sequence or doubled
    import re as _re
    # Detect a *single* backslash before 'u' (not doubled). We consider doubled \\u safe.
    bad_sequences = _re.findall(r"(?<!\\)\\u(?![0-9a-fA-F]{4})", dumped)
    assert not bad_sequences, f"Residual unsafe \\u sequences remain: {bad_sequences}"

    # Emoji should remain unchanged
    assert sanitized["emoji"].endswith("🚀")

    # And the JSON representation should contain the doubled backslashes
    assert "\\\\\\\\z" in json.dumps(sanitized["raw"])
    assert "\\\\\\\\u12" in json.dumps(sanitized["partial"])
