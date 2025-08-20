import pathlib
import sys

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
from app.services.conversation import route_mode


def test_trivial_mode_for_short_prompt():
    mode = route_mode("Hi", has_tools=False, constraints={})
    assert mode == "trivial"


@pytest.mark.parametrize(
    "prompt",
    [
        "Can you plan a trip for me?",
        "Explique pourquoi le ciel est bleu.",
    ],
)
def test_deep_mode_with_markers(prompt):
    mode = route_mode(prompt, has_tools=False, constraints={})
    assert mode == "deep"


def test_standard_mode_with_markers_and_latency():
    prompt = "Explique pourquoi le ciel est bleu."
    mode = route_mode(prompt, has_tools=False, constraints={"maxLatencyMs": 1000})
    assert mode == "standard"


def test_tools_mode_when_tools_allowed():
    mode = route_mode(
        "Use tools please",
        has_tools=True,
        constraints={},
        allowed_tools=["search"],
    )
    assert mode == "tools"
