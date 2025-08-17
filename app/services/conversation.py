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


def resolve_special_model(model: Optional[str], fallback: Optional[str] = None) -> Optional[str]:
    """Translate special aliases to concrete deployment names.

    If an alias is provided but the corresponding environment variable is unset,
    return the fallback so classic models still work.
    """
    if model == "model-router":
        return os.getenv("MODEL_ROUTER_DEPLOYMENT") or fallback
    if model == "gpt-oss-120b":
        return os.getenv("GPT_OSS_120B_DEPLOYMENT") or fallback
    return model or fallback


def select_model_and_effort(
    prompt: str, default_model: Optional[str] = None, default_effort: str = "low"
) -> Tuple[str, str]:
    base_default = resolve_special_model(os.getenv("AZURE_OPENAI_MODEL"), "gpt-5-mini")
    model = resolve_special_model(default_model, base_default) or base_default
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
        # Always include MCP if provided (ensures explicitly autorisés comme "hello_mcp")
        if mcp_tool_cfg:
            tools.append(mcp_tool_cfg)
        # Then include built-in tools (e.g., search_web)
        if builtin_tools:
            tools.extend(builtin_tools)
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
    # Extract internal-only fields that must not be sent to the API
    internal_user_id = None
    try:
        internal_user_id = str(responses_args.pop("x_user_id", "")).strip() or None
    except Exception:
        internal_user_id = None
    response = client.responses.create(**responses_args)
    # Safety loop to avoid infinite cycles
    executed_any_tool = False
    used_tools: List[Dict[str, Any]] = []
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
                try:
                    used_tools.append({"name": name, "arguments": args, "type": "classic"})
                except Exception:
                    pass
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
            # Extract last user text once for heuristics
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

            # 1) Heuristic realtime websearch
            has_search = any((t.get("function", {}).get("name") == "search_web" or t.get("name") == "search_web") for t in tools)
            try:
                text_l = (user_text or "").lower()
                realtime_markers = ("météo", "meteo", "weather", "aujourd'hui", "now", "today", "breaking", "news", "actu", "actualité")
                if has_search and user_text and any(k in text_l for k in realtime_markers):
                    direct = execute_tool_call("search_web", {"query": user_text})
                    if isinstance(direct, str) and direct.strip():
                        output_text = direct
                        fallback_text = direct
                        try:
                            used_tools.append({"name": "search_web", "arguments": {"query": user_text}, "type": "classic", "direct": True})
                        except Exception:
                            pass
            except Exception:
                pass

            # 2) Heuristic document-service classic tools
            try:
                # Check that any doc-service tool is available
                available_names = set()
                for t in tools:
                    nm = t.get("name") or t.get("function", {}).get("name")
                    if nm:
                        available_names.add(nm)
                has_docsvc = any(n in available_names for n in (
                    "convert_word_to_pdf",
                    "init_user",
                    "list_images",
                    "list_shared_templates",
                    "list_templates_http",
                ))
                if has_docsvc and user_text:
                    lower = (user_text or "").lower()
                    import re  # local import
                    url_match = re.search(r"https?://\S+", user_text or "")
                    found_url = url_match.group(0) if url_match else None
                    user_id_for_tools = internal_user_id

                    fallback_chunks: List[str] = []

                    def set_fallback_and_note(name: str, args: Dict[str, Any], output: str) -> None:
                        nonlocal output_text, fallback_text, used_tools
                        # Accumulate multiple tool outputs; summarize afterwards
                        try:
                            label = name
                            if name == "list_shared_templates":
                                label = "shared_templates"
                            elif name == "list_templates_http":
                                label = "user_templates"
                            elif name == "list_images":
                                label = "user_images"
                            chunk = f"<{label}>\n{output}\n</{label}>"
                            fallback_chunks.append(chunk)
                        except Exception:
                            fallback_chunks.append(str(output))
                        try:
                            used_tools.append({"name": name, "arguments": args, "type": "classic", "direct": True})
                        except Exception:
                            pass

                    # init_user
                    if ("init" in lower or "initialize" in lower or "initialiser" in lower or "initialisé" in lower or "initialise" in lower) and ("container" in lower or "blob" in lower):
                        if user_id_for_tools and ("init_user" in available_names):
                            args = {"user_id": user_id_for_tools}
                            direct = execute_tool_call("init_user", args)
                            if isinstance(direct, str) and direct.strip():
                                set_fallback_and_note("init_user", args, direct)

                    # list_images (avoid matching when prompt requests initialization)
                    if ("list" in lower or "lister" in lower or "voir" in lower or "mes images" in lower or "images" in lower) \
                        and ("image" in lower or "images" in lower) and not ("init" in lower or "initialize" in lower or "initialiser" in lower):
                        if user_id_for_tools and ("list_images" in available_names):
                            args = {"user_id": user_id_for_tools}
                            direct = execute_tool_call("list_images", args)
                            if isinstance(direct, str) and direct.strip():
                                set_fallback_and_note("list_images", args, direct)

                    # list_shared_templates
                    if ("template" in lower or "templates" in lower) and ("partagé" in lower or "partages" in lower or "shared" in lower):
                        if "list_shared_templates" in available_names:
                            args = {}
                            direct = execute_tool_call("list_shared_templates", args)
                            if isinstance(direct, str) and direct.strip():
                                set_fallback_and_note("list_shared_templates", args, direct)

                    # list_templates_http (user templates)
                    if ("template" in lower or "templates" in lower) and ("mes" in lower or "my" in lower):
                        if user_id_for_tools and ("list_templates_http" in available_names):
                            args = {"user_id": user_id_for_tools}
                            direct = execute_tool_call("list_templates_http", args)
                            if isinstance(direct, str) and direct.strip():
                                set_fallback_and_note("list_templates_http", args, direct)

                    # upload_* tools removed

                    # convert_word_to_pdf
                    if ("convert" in lower or "convertir" in lower) and ("docx" in lower or "doc" in lower) and ("pdf" in lower):
                        if ("convert_word_to_pdf" in available_names):
                            # Accept either a blob-like filename or a blob path
                            # Prefer explicit blob path if present; otherwise prefix with user_id
                            filename_match = re.search(r"([\w\-./]+\.(?:docx|doc))", user_text or "", flags=re.IGNORECASE)
                            blob_candidate = filename_match.group(1) if filename_match else None
                            if blob_candidate:
                                blob_path = blob_candidate if ("/" in blob_candidate) else (f"{user_id_for_tools}/{blob_candidate}" if user_id_for_tools else None)
                                if blob_path:
                                    args = {"blob": blob_path}
                                    direct = execute_tool_call("convert_word_to_pdf", args)
                                    if isinstance(direct, str) and direct.strip():
                                        set_fallback_and_note("convert_word_to_pdf", args, direct)
                    # If any chunks gathered, set fallback_text from all
                    try:
                        if fallback_chunks:
                            combined = "\n\n".join(fallback_chunks)
                            # only set if not already set by realtime websearch
                            if not (isinstance(fallback_text, str) and fallback_text.strip()):
                                fallback_text = combined
                            else:
                                fallback_text = f"{fallback_text}\n\n{combined}"
                    except Exception:
                        pass
            except Exception:
                pass
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
                    "You received tool results as tagged blocks in <context>. "
                    "Return a compact answer enumerating concrete items, with no extra prose. "
                    "If the block <shared_templates> exists, output a line: 'Shared templates: name1, name2'. "
                    "If the block <user_templates> exists, output a line: 'My templates: name1, name2'. "
                    "Extract names by parsing JSON when present (use the 'name' property if available); if plain text, list each line as-is. "
                    "If a list is empty or missing, write 'none' after the label. Do not mention 'context' or sources.\n\n"
                    f"User question: {user_text}\n\n<context>\n{fallback_text}\n</context>\n"
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
                # Post-synthesis should not trigger more tool calls; ask for summary only
                final_resp = client.responses.create(**args2)
                final_text = getattr(final_resp, "output_text", None)
                if final_text and final_text.strip():
                    output_text = final_text
                    # Merge tool usage metadata from both passes
                    try:
                        final_used = getattr(final_resp, "_classic_tools_used", None)
                        if isinstance(final_used, list):
                            used_tools.extend(final_used)
                    except Exception:
                        pass
                    response = final_resp
                else:
                    # Fallback: ensure we synthesize a textual answer without tools
                    try:
                        no_tools_args2 = dict(args2)
                        no_tools_args2.pop("tools", None)
                        no_tools_args2.pop("tool_choice", None)
                        direct_resp = client.responses.create(**no_tools_args2)
                        direct_text = getattr(direct_resp, "output_text", None)
                        if direct_text and direct_text.strip():
                            output_text = direct_text
                            response = direct_resp
                    except Exception:
                        pass
            except Exception:
                logging.exception("post-synthesis second pass failed; returning fallback text")
                # Keep output_text as fallback
    except Exception:
        pass
    # Attach metadata of used classic tools to the response object for downstream HTTP handlers
    try:
        setattr(response, "_classic_tools_used", used_tools)
    except Exception:
        pass
    return output_text, response


def build_system_message_text() -> str:
    return _load_system_prompt_markdown()


# --- System prompt loading (markdown with optional remote override) -----------------------------
_SYSTEM_PROMPT_CACHE: Optional[str] = None
_SYSTEM_PROMPT_FETCHED_AT: Optional[float] = None


def _load_system_prompt_markdown() -> str:
    """Load system prompt from markdown file or remote URL, with simple in-memory caching.

    - SYSTEM_PROMPT_PATH: local file path (default: "system_prompt.md")
    - SYSTEM_PROMPT_URL: optional remote URL to fetch markdown
    - SYSTEM_PROMPT_TTL_SECONDS: cache TTL for remote fetch (default: 300)

    Supports token replacement: {{today}} will be replaced with ISO date.
    Fallbacks to a minimal built-in prompt if nothing is configured.
    """
    global _SYSTEM_PROMPT_CACHE, _SYSTEM_PROMPT_FETCHED_AT
    today = datetime.date.today().isoformat()
    # Remote first when configured
    url = os.getenv("SYSTEM_PROMPT_URL")
    if url:
        try:
            import time as _time  # local alias
            ttl = int(os.getenv("SYSTEM_PROMPT_TTL_SECONDS", "300"))
            now = _time.time()
            if _SYSTEM_PROMPT_CACHE is not None and _SYSTEM_PROMPT_FETCHED_AT and (now - _SYSTEM_PROMPT_FETCHED_AT < max(5, ttl)):
                return _SYSTEM_PROMPT_CACHE.replace("{{today}}", today)
            # Lazy import requests
            try:
                import requests  # type: ignore
            except Exception:
                requests = None  # type: ignore
            if requests:
                resp = requests.get(url, timeout=5)
                if resp.status_code < 400:
                    text = str(resp.text or "").strip()
                    if text:
                        _SYSTEM_PROMPT_CACHE = text
                        _SYSTEM_PROMPT_FETCHED_AT = now
                        return text.replace("{{today}}", today)
        except Exception:
            pass
    # Local file next
    path = os.getenv("SYSTEM_PROMPT_PATH", "system_prompt.md")
    try:
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                md = f.read()
                return md.replace("{{today}}", today)
    except Exception:
        pass
    # Built-in minimal fallback
    base = (
        "You are a helpful assistant. Prefer prior conversation context to disambiguate. "
        f"Current date: {today}. "
    )
    try:
        from .tools import get_builtin_tools_config
        tools = get_builtin_tools_config()
        has_search = any((t.get("name") == "search_web") for t in tools)
    except Exception:
        has_search = False
    if has_search:
        base += "Use the 'search_web' tool for time-sensitive questions (weather, news, live results, availability)."
    return base

