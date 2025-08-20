import os
from typing import List, Optional


def _orchestrator_models() -> dict:
    return {
        "trivial": os.getenv("ORCHESTRATOR_MODEL_TRIVIAL"),
        "standard": os.getenv("ORCHESTRATOR_MODEL_STANDARD"),
        "tools": os.getenv("ORCHESTRATOR_MODEL_TOOLS"),
        "deep": os.getenv("ORCHESTRATOR_MODEL_REASONING"),
    }


def _route_mode(
    prompt: str,
    has_tools: bool,
    constraints: dict,
    allowed_tools: Optional[List[str]] = None,
) -> str:
    # Only select tools mode if caller explicitly allows tools
    if has_tools and allowed_tools:
        return "tools"
    # Accept both camelCase and snake_case flags and flat boolean values
    prefer_reasoning = False
    try:
        prefer_reasoning = (
            str(constraints.get("preferReasoning", "")).lower() in ("1", "true", "yes", "on")
            or str(constraints.get("prefer_reasoning", "")).lower() in ("1", "true", "yes", "on")
        )
    except Exception:
        prefer_reasoning = False
    try:
        max_latency_ms = (
            int(constraints.get("maxLatencyMs"))
            if constraints.get("maxLatencyMs") is not None
            else None
        )
    except Exception:
        max_latency_ms = None
    text = (prompt or "").lower()
    # Include French markers so FR prompts can trigger reasoning automatically
    deep_markers = (
        "plan",
        "multi-step",
        "derive",
        "prove",
        "why",
        "strategy",
        "chain of thought",
        "plan d'action",
        "multi-etapes",
        "multi étapes",
        "démontrer",
        "demontrer",
        "prouve",
        "pourquoi",
        "stratégie",
        "strategie",
        "raisonnement",
        "chaine de raisonnement",
        "chaîne de raisonnement",
        "réfléchis",
        "reflechis",
        "pas à pas",
        "pas a pas",
        "analyse détaillée",
        "explication détaillée",
    )
    if prefer_reasoning or any(m in text for m in deep_markers) or len(prompt) > 800:
        # If explicit latency budget is tight, downshift to standard
        if max_latency_ms is not None and max_latency_ms < 1500:
            return "standard"
        return "deep"
    # length-based quick rule
    if len(prompt) < 160:
        return "trivial"
    return "standard"

