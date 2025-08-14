import os
import json
from typing import Any, Dict, Optional, List, Tuple


def normalize_allowed_tools(raw_value: Any) -> Optional[List[str]]:
    try:
        if isinstance(raw_value, list):
            if len(raw_value) == 1 and isinstance(raw_value[0], str):
                single = raw_value[0].strip()
                if single.startswith("[") and single.endswith("]"):
                    parsed = json.loads(single)
                    return [str(x) for x in parsed if isinstance(x, (str, int, float))]
                if "," in single:
                    return [p.strip() for p in single.split(",") if p.strip()]
            return [str(x) for x in raw_value if isinstance(x, (str, int, float))]
        if isinstance(raw_value, str):
            trimmed = raw_value.strip()
            if trimmed.startswith("[") and trimmed.endswith("]"):
                parsed = json.loads(trimmed)
                return [str(x) for x in parsed if isinstance(x, (str, int, float))]
            if "," in trimmed:
                return [p.strip() for p in trimmed.split(",") if p.strip()]
            if trimmed:
                return [trimmed]
    except Exception:
        pass
    return None


def resolve_mcp_config(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Prefer locked server-to-server config
    # Allow override via request only when explicitly enabled
    allow_override = str(os.getenv("ALLOW_CLIENT_MCP_OVERRIDE", "false")).lower() in ("1", "true", "yes", "on")

    if allow_override and body.get("mcp_url"):
        server_url = body.get("mcp_url")
        headers: Optional[Dict[str, str]] = None
        if isinstance(body.get("mcp_headers"), dict):
            headers = {str(k): str(v) for k, v in body["mcp_headers"].items()}
        else:
            key = body.get("mcp_key")
            if key:
                headers = {"x-functions-key": key}
    else:
        server_url = os.getenv("TOOLS_SSE_URL") or os.getenv("LOCAL_MCP_SSE_URL") or os.getenv("MCP_SSE_URL")
        key = os.getenv("TOOLS_FUNCTIONS_KEY") or os.getenv("LOCAL_MCP_FUNCTIONS_KEY") or os.getenv("MCP_SSE_KEY")
        headers = {"x-functions-key": key} if key else None

    if not server_url:
        # If allowed_tools is empty or explicitly disabled, we return None to run without tools
        normalized_allowed = normalize_allowed_tools(body.get("allowed_tools"))
        if not normalized_allowed:
            return None
        # Otherwise, still error because tools were requested but no server URL is configured
        raise ValueError("Missing MCP SSE URL. Configure TOOLS_SSE_URL or LOCAL_MCP_SSE_URL/MCP_SSE_URL.")

    normalized_allowed = normalize_allowed_tools(body.get("allowed_tools"))
    # If caller explicitly sets an empty list, disable tools
    if isinstance(body.get("allowed_tools"), list) and len(body.get("allowed_tools")) == 0:
        return None
    allowed_tools = normalized_allowed or ["hello_mcp", "get_snippet", "save_snippet"]

    require_approval = body.get("require_approval") or "never"
    return {
        "type": "mcp",
        "server_label": body.get("server_label") or ("remote-mcp-function" if server_url.startswith("https://") else "local-mcp-function"),
        "server_url": server_url,
        "allowed_tools": allowed_tools,
        "require_approval": require_approval,
        **({"headers": headers} if headers else {}),
    }


# --- Built-in classic tools (non-MCP) ---------------------------------------------------------

def _websearch_env() -> Tuple[Optional[str], Optional[str]]:
    url = os.getenv("WEBSEARCH_FUNCTION_URL")
    key = os.getenv("WEBSEARCH_FUNCTION_KEY")
    return (url, key)


def _websearch_enabled() -> bool:
    flag = str(os.getenv("ENABLE_WEBSEARCH_TOOL", "true")).lower() in ("1", "true", "yes", "on")
    url, _ = _websearch_env()
    return flag and bool(url)


def _build_search_web_tool_def() -> Dict[str, Any]:
    return {
        "type": "function",
        "name": "search_web",
        "description": (
            "Perform a web search via Azure Function (SearXNG). "
            "Provide 'query' in English to improve recall; the service will choose focus mode automatically."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "description": "English search query."}
            },
            "required": ["query"]
        }
    }


def get_builtin_tools_config() -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    if _websearch_enabled():
        tools.append(_build_search_web_tool_def())
    return tools


def has_builtin_tools() -> bool:
    return len(get_builtin_tools_config()) > 0


def _call_websearch_backend(args: Dict[str, Any]) -> str:
    url, key = _websearch_env()
    if not url:
        return "Websearch backend not configured."
    # Build URL with key only if not already embedded
    final_url = url
    if key and ("?code=" not in url) and ("&code=" not in url):
        sep = "&" if ("?" in url) else "?"
        final_url = f"{url}{sep}code={key}"
    payload: Dict[str, Any] = {}
    if isinstance(args, dict):
        english_query = (args.get("query") or "").strip()
        if english_query:
            payload["query"] = english_query
        # Heuristic focus mode selection when possible
        lower = english_query.lower()
        if any(k in lower for k in ("weather", "météo", "today", "now", "forecast", "news")):
            payload["focus_mode"] = "newsSearch"
        else:
            payload["focus_mode"] = "webSearch"
        # Provide a sane default for max_results
        payload["max_results"] = 5
    # Lazy import to avoid module-level failure
    try:
        import requests  # type: ignore
    except Exception as e:
        return f"requests import failed: {e}"
    try:
        headers = {"Content-Type": "application/json"}
        resp = requests.post(final_url, headers=headers, data=json.dumps(payload), timeout=30)
        text = resp.text or ""
        try:
            data = resp.json()
        except Exception:
            data = None
        if resp.status_code >= 400:
            return f"websearch error {resp.status_code}: {text[:500]}"
        # Prefer structured fields when available
        if isinstance(data, dict):
            for key in ("output_text", "summary", "result", "content"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val
            # Fallback: pretty-print compact json
            try:
                return json.dumps(data, ensure_ascii=False)
            except Exception:
                return text
        return text
    except Exception as e:
        return f"websearch call failed: {e}"


def execute_tool_call(name: str, arguments: Dict[str, Any]) -> str:
    tool = (name or "").strip().lower()
    if tool in ("search_web", "websearch"):
        return _call_websearch_backend(arguments or {})
    return f"Unknown tool: {name}"

