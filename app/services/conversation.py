import os
import time
import uuid
import json
import logging
import datetime
from typing import Any, Dict, Optional, List, Tuple


def create_llm_client():
    try:
        from openai import AzureOpenAI, OpenAI
    except Exception as e:
        raise RuntimeError("The 'openai' package is required. Add it to requirements.txt and deploy.") from e

    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    azure_key = os.getenv("AZURE_OPENAI_KEY")
    if azure_endpoint and azure_key:
        return AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=azure_key,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
        )

    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        raise RuntimeError("Missing OPENAI_API_KEY or AZURE_OPENAI_* settings.")
    return OpenAI(api_key=openai_key)


def select_model_and_effort(prompt: str, default_model: Optional[str] = None, default_effort: str = "low") -> Tuple[str, str]:
    model = default_model or os.getenv("AZURE_OPENAI_MODEL") or "gpt-5-mini"
    effort = default_effort
    # Heuristique simple (placeholder) : on peut brancher un parser RULES plus tard
    if len(prompt) < 160:
        effort = os.getenv("DEFAULT_REASONING_EFFORT", effort)
    else:
        effort = os.getenv("DEFAULT_REASONING_EFFORT", effort)
    return model, effort


def _parse_reasoning_models() -> List[str]:
    raw = os.getenv("REASONING_MODELS", "").strip()
    if not raw:
        return []
    return [m.strip() for m in raw.split(",") if m.strip()]


def _supports_reasoning(model: str) -> bool:
    allow_list = _parse_reasoning_models()
    if allow_list:
        return model in allow_list
    # Fallback heuristic: only explicitly known reasoning families
    lower = (model or "").lower()
    return lower.startswith("o3") or lower.startswith("o4") or ("-r" in lower)


def build_responses_args(
    model: str,
    prompt: str,
    mcp_tool_cfg: Optional[Dict[str, Any]],
    reasoning_effort: str,
) -> Dict[str, Any]:
    from .tools import get_builtin_tools_config
    args: Dict[str, Any] = {
        "model": model,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "text": {"format": {"type": "text"}, "verbosity": "medium"},
        "store": False,
    }
    if _supports_reasoning(model):
        args["reasoning"] = {"effort": reasoning_effort}
    tools: List[Dict[str, Any]] = []
    try:
        builtin_tools = get_builtin_tools_config()
        if builtin_tools:
            # Prioritize builtin tools first
            tools.extend(builtin_tools)
            # Optionally include MCP alongside builtin tools when explicitly allowed
            include_mcp = str(os.getenv("INCLUDE_MCP_WITH_BUILTIN", "false")).lower() in ("1", "true", "yes", "on")
            if include_mcp and mcp_tool_cfg:
                tools.append(mcp_tool_cfg)
        elif mcp_tool_cfg:
            tools.append(mcp_tool_cfg)
    except Exception:
        pass
    if tools:
        args["tools"] = tools
        args["tool_choice"] = "auto"
    return args


def run_with_optional_stream(client, responses_args: Dict[str, Any], stream: bool = False) -> Tuple[Optional[str], Any]:
    output_text: Optional[str] = None
    if stream:
        try:
            chunks: List[str] = []
            with client.responses.stream(**responses_args) as s:
                for event in s:
                    if getattr(event, "type", None) == "response.output_text.delta":
                        delta = getattr(event, "delta", "")
                        if delta:
                            chunks.append(delta)
                            logging.info(delta)
                final = s.get_final_response()
            output_text = "".join(chunks) or getattr(final, "output_text", None)
            return output_text, final
        except Exception:
            logging.exception("streaming error; fallback to non-stream")
    resp = client.responses.create(**responses_args)
    output_text = getattr(resp, "output_text", None)
    return output_text, resp


def run_responses_with_tools(
    client,
    responses_args: Dict[str, Any],
    allow_post_synthesis: bool = True,
) -> Tuple[Optional[str], Any]:
    """
    Execute a Responses request that may include classic function tools. Handles the
    requires_action -> submit_tool_outputs loop until completion.
    """
    from .tools import execute_tool_call, get_builtin_tools_config
    # Never stream here; tool loop requires synchronous handling
    # Ensure using a tools-capable model when tools are attached
    try:
        if responses_args.get("tools") and not responses_args.get("model"):
            responses_args["model"] = os.getenv("ORCHESTRATOR_MODEL_TOOLS", responses_args.get("model", "gpt-4.1"))
    except Exception:
        pass
    response = client.responses.create(**responses_args)
    # Safety loop to avoid infinite cycles
    executed_any_tool = False
    fallback_text: Optional[str] = None
    for _ in range(6):
        status = getattr(response, "status", None)
        if status != "requires_action":
            break
        required = getattr(response, "required_action", None)
        submit = getattr(required, "submit_tool_outputs", None) if required else None
        calls = getattr(submit, "tool_calls", None) if submit else None
        tool_outputs: List[Dict[str, str]] = []
        if not calls:
            break
        for call in calls:
            try:
                call_id = getattr(call, "id", None) or ""
                func_obj = getattr(call, "function", None)
                name = getattr(func_obj, "name", None) or ""
                raw_args = getattr(func_obj, "arguments", None) or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except Exception:
                    args = {}
                output = execute_tool_call(name, args)
                tool_outputs.append({"tool_call_id": call_id, "output": output})
                executed_any_tool = True
            except Exception:
                logging.exception("tool execution failed; returning error text to model")
                tool_outputs.append({"tool_call_id": getattr(call, "id", ""), "output": "Tool execution failed."})
        response = client.responses.submit_tool_outputs(
            response_id=getattr(response, "id", None), tool_outputs=tool_outputs
        )
    output_text = getattr(response, "output_text", None)
    # Heuristic realtime fallback: if websearch available but no tool call occurred, and prompt looks realtime, call it directly
    try:
        if allow_post_synthesis and (not executed_any_tool):
            tools = get_builtin_tools_config()
            has_search = any((t.get("function", {}).get("name") == "search_web" or t.get("name") == "search_web") for t in tools)
            if has_search:
                user_text: Optional[str] = None
                try:
                    msgs = responses_args.get("input") or []
                    if isinstance(msgs, list):
                        for m in reversed(msgs):
                            if m.get("role") == "user":
                                parts = m.get("content") or []
                                for p in parts:
                                    if isinstance(p, dict) and p.get("type") == "input_text":
                                        user_text = p.get("text")
                                        break
                                if user_text:
                                    break
                except Exception:
                    user_text = None
                text_l = (user_text or "").lower()
                realtime_markers = ("météo", "meteo", "weather", "aujourd'hui", "now", "today", "breaking", "news", "actu", "actualité")
                if user_text and any(k in text_l for k in realtime_markers):
                    direct = execute_tool_call("search_web", {"query": user_text})
                    if isinstance(direct, str) and direct.strip():
                        output_text = direct
                        fallback_text = direct
    except Exception:
        pass
    try:
        # Post-synthesis second pass (single-shot): feed results back to the model and allow additional tools
        if allow_post_synthesis and fallback_text and isinstance(fallback_text, str) and fallback_text.strip():
            try:
                model = responses_args.get("model")
                # Rebuild input with system guidance and context block
                system_msg = build_system_message_text()
                user_text: str = ""
                try:
                    msgs = responses_args.get("input") or []
                    if isinstance(msgs, list):
                        for m in reversed(msgs):
                            if m.get("role") == "user":
                                parts = m.get("content") or []
                                texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "input_text"]
                                if texts:
                                    user_text = texts[-1]
                                    break
                except Exception:
                    user_text = ""
                summary_prompt = (
                    "Tu as reçu des résultats de recherche (voir le bloc <context>). "
                    "Fourni une réponse synthétique et précise à la question de l'utilisateur, en citant les sources si possible.\n" 
                    "N'hésite pas à utiliser d'autres outils si nécessaire. Réponds en français.\n\n"
                    f"Question utilisateur : {user_text}\n\n<context>\n{fallback_text}\n</context>\n"
                )
                args2: Dict[str, Any] = {
                    "model": model,
                    "input": [
                        {"role": "system", "content": [{"type": "input_text", "text": system_msg}]},
                        {"role": "user", "content": [{"type": "input_text", "text": summary_prompt}]},
                    ],
                    "text": {"format": {"type": "text"}, "verbosity": "medium"},
                    "store": False,
                }
                # Reuse tools to allow multi-tool flow during post-synthesis
                if responses_args.get("tools"):
                    args2["tools"] = responses_args["tools"]
                    args2["tool_choice"] = "auto"
                # Run a full tool loop again to allow additional tools if needed
                final_text, final_resp = run_responses_with_tools(client, args2, allow_post_synthesis=False)
                if final_text and final_text.strip():
                    output_text = final_text
                    response = final_resp
            except Exception:
                logging.exception("post-synthesis second pass failed; returning fallback text")
                # Keep output_text as fallback
    except Exception:
        pass
    return output_text, response


def build_system_message_text() -> str:
    today = datetime.date.today().isoformat()
    base = (
        "Tu es un assistant conversationnel. Utilise strictement le contexte des tours précédents. "
        "En cas d'ambiguïté, suppose que l'utilisateur parle du même sujet que précédemment. Réponds en français. "
        f"Date actuelle: {today}. "
    )
    # Si des tools classiques sont disponibles, instruis le modèle sur leur usage
    try:
        from .tools import get_builtin_tools_config
        tools = get_builtin_tools_config()
        has_search = any((t.get("name") == "search_web") for t in tools)
    except Exception:
        has_search = False
    if has_search:
        base += (
            "Utilise systématiquement l'outil 'search_web' pour toute question dépendant du temps réel (météo, actualités, résultats en cours, disponibilités). "
            "Traduis la requête utilisateur en anglais avant l'appel et fournis-la comme 'query'."
        )
    return base

